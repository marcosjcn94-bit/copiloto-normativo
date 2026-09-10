"""A fiação: configuração, serviço e o app do FastAPI.

O que este módulo resolve é o que `rotas.py` deliberadamente não sabe: qual
provedor de LLM subir, onde estão os índices, quem escreve no CRM e onde fica o
checkpoint que faz o `interrupt()` da Fase 5 valer entre uma requisição e outra.

**A conversa vive no checkpoint, não no processo.** `POST /perguntar` devolve o
`thread_id`; `POST /aprovar` volta com ele e o grafo retoma de onde parou. Não há
sessão em memória, então reiniciar a API no meio de uma aprovação pendente não
perde nada — é o mesmo mecanismo que o teste de retomada da Fase 5 exercita, só
que agora com HTTP entre as duas metades.

**Uma execução por vez, e isso é escolha.** A máquina do projeto tem 8 GB e a
regra do §3.1 é um único modelo ONNX carregado por vez. Um `Lock` global serializa
as execuções do grafo: duas consultas simultâneas usariam o mesmo embedder e o
mesmo reranker ao mesmo tempo, e o mesmo arquivo SQLite de checkpoint. Vazão não é
requisito deste sistema; previsibilidade de memória é.

**O registro de tools é montado por requisição.** `montar_registro` recebe
`thread_id` e o passo corrente porque os dois entram na chave de idempotência. Um
registro global guardaria o `thread_id` da primeira conversa e daria a mesma chave
para consultas diferentes — que é exatamente o bug que a idempotência existe para
evitar.

Como subir:

    .venv/Scripts/python.exe -m uvicorn copiloto.api.main:criar_app --factory --port 8000
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from langgraph.types import Command

from copiloto.api.rotas import (
    ConsultaNaoEncontrada,
    Copiloto,
    Decisao,
    NadaAAprovar,
    SaidaConsulta,
    SaidaDecisao,
    SaidaSaude,
    criar_router,
    registrar_tratadores,
)
from copiloto.grafo.grafo import Dependencias, checkpointer_sqlite, compilar
from copiloto.grafo.nos import ParametrosDoGrafo
from copiloto.grafo.nos import carregar_parametros as parametros_do_grafo
from copiloto.llm.groq import ProvedorGroq
from copiloto.llm.provedor import ProvedorLLM
from copiloto.recuperacao.retriever import Recuperador
from copiloto.tools.buscar_normativo import FonteDeTrechos
from copiloto.tools.registrar_crm import enviar_registro
from copiloto.tools.registro import ParametrosDeTools, cliente_de_crm, montar_registro
from copiloto.tools.registro import carregar_parametros as parametros_de_tools
from copiloto.tools.schemas import PropostaDeRegistro, SaidaRegistrarCrm

logger = logging.getLogger(__name__)

RAIZ_PADRAO = Path(__file__).resolve().parents[3]

Escritor = Callable[[PropostaDeRegistro], SaidaRegistrarCrm]


def versao_do_pacote() -> str:
    try:
        return version("copiloto-normativo")
    except PackageNotFoundError:  # rodando da árvore sem instalar
        return "0.0.0+dev"


@dataclass(frozen=True, slots=True)
class Configuracao:
    """De onde vem cada caminho e cada URL. Nada disso é constante no Python."""

    raiz: Path = RAIZ_PADRAO
    provedor: str = "groq"
    crm_base_url: str = ""
    caminho_checkpoint: Path = Path("data/checkpoints.sqlite")
    caminho_catalogo: Path = Path("data/catalogo.sqlite")

    @classmethod
    def do_ambiente(
        cls, ambiente: Mapping[str, str] | None = None, *, raiz: Path = RAIZ_PADRAO
    ) -> Configuracao:
        """Lê o `.env`. Caminho relativo é resolvido contra a raiz do repositório.

        `DATABASE_URL` não é lida aqui: no dev ela é sempre o mesmo SQLite que a
        Fase 2 escreveu, e é a Fase 8 que troca o destino por PostgreSQL. Ler
        agora uma variável que ainda não muda nada seria configuração de mentira.
        """
        amb = ambiente if ambiente is not None else os.environ
        return cls(
            raiz=raiz,
            provedor=amb.get("LLM_PROVIDER", "groq").strip().lower() or "groq",
            crm_base_url=amb.get("CRM_BASE_URL", "").strip(),
            caminho_checkpoint=cls._caminho(
                raiz, amb.get("CHECKPOINT_PATH", "data/checkpoints.sqlite")
            ),
            caminho_catalogo=cls._caminho(raiz, "data/catalogo.sqlite"),
        )

    @staticmethod
    def _caminho(raiz: Path, valor: str) -> Path:
        caminho = Path(valor)
        return caminho if caminho.is_absolute() else raiz / caminho


def provedor_do_ambiente(
    nome: str, ambiente: Mapping[str, str] | None = None, **extras: Any
) -> ProvedorLLM:
    """Escolhe a implementação de `ProvedorLLM` por `LLM_PROVIDER`.

    Falha alto e cedo quando o nome não tem implementação: `azure_openai.py` e
    `ollama.py` estão previstos no §5 e ainda não existem neste repositório.
    Cair silenciosamente na Groq faria o `.env` mentir sobre qual sistema atendeu
    — e é justamente esse dado que a Fase 9 audita no inventário.
    """
    if nome == "groq":
        return ProvedorGroq.do_ambiente(ambiente, **extras)
    raise ValueError(
        f"LLM_PROVIDER={nome!r} não tem implementação neste repositório; hoje só a Groq"
    )


class Servico:
    """A implementação de `Copiloto`: dono do grafo, dos índices e do CRM.

    O construtor recebe tudo pronto; `abrir` é quem faz o trabalho caro. A
    separação é o que permite ao teste montar um serviço com recuperador de
    mentira sem carregar um único modelo ONNX.
    """

    def __init__(
        self,
        *,
        provedor: ProvedorLLM,
        recuperador: FonteDeTrechos,
        catalogo: sqlite3.Connection,
        parametros_grafo: ParametrosDoGrafo,
        parametros_tools: ParametrosDeTools,
        checkpointer: Any,
        caminho_checkpoint: Path,
        crm_base_url: str = "",
        cliente_crm: httpx.Client | None = None,
        fechaveis: tuple[Any, ...] = (),
    ) -> None:
        self._provedor = provedor
        self._recuperador = recuperador
        self._catalogo = catalogo
        self._parametros_grafo = parametros_grafo
        self._parametros_tools = parametros_tools
        self._checkpointer = checkpointer
        self._caminho_checkpoint = caminho_checkpoint
        self._crm_base_url = crm_base_url
        self._cliente_crm = cliente_crm
        self._fechaveis = fechaveis
        self._trava = threading.Lock()

    # --- construção ----------------------------------------------------------

    @classmethod
    def abrir(cls, configuracao: Configuracao) -> Servico:
        """Sobe tudo o que a API precisa. Índice ausente ou chave faltando falha aqui."""
        toml = configuracao.raiz / "config" / "parametros.toml"
        catalogo = sqlite3.connect(configuracao.caminho_catalogo, check_same_thread=False)
        catalogo.row_factory = sqlite3.Row
        recuperador = Recuperador.abrir(configuracao.raiz, catalogo=catalogo)

        parametros_tools = parametros_de_tools(toml)
        checkpointer, conexao = checkpointer_sqlite(configuracao.caminho_checkpoint)
        cliente_crm = (
            cliente_de_crm(configuracao.crm_base_url, parametros_tools.timeout_crm)
            if configuracao.crm_base_url
            else None
        )
        return cls(
            provedor=provedor_do_ambiente(configuracao.provedor),
            recuperador=recuperador,
            catalogo=catalogo,
            parametros_grafo=parametros_do_grafo(toml),
            parametros_tools=parametros_tools,
            checkpointer=checkpointer,
            caminho_checkpoint=configuracao.caminho_checkpoint,
            crm_base_url=configuracao.crm_base_url,
            cliente_crm=cliente_crm,
            fechaveis=(catalogo, conexao, cliente_crm),
        )

    def fechar(self) -> None:
        """Quem abriu fecha. No Windows, conexão SQLite viva trava o arquivo."""
        for recurso in self._fechaveis:
            if recurso is None:
                continue
            try:
                recurso.close()
            except Exception:  # noqa: BLE001 — desligar não pode falhar o desligamento
                logger.warning("falha ao fechar recurso", extra={"recurso": type(recurso).__name__})

    # --- grafo ---------------------------------------------------------------

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _passo_seguinte(self, thread_id: str) -> int:
        """O passo que a próxima deliberação vai gastar — ingrediente da chave.

        Sai do checkpoint, não de um contador em memória: só assim duas metades da
        mesma conversa separadas por um reinício de processo continuam produzindo
        a mesma chave de idempotência para os mesmos argumentos. Dentro de uma
        requisição o valor não se move, e isso é aceitável de propósito: o que a
        chave precisa garantir é que o mesmo pedido não vire dois registros, não
        que dois pedidos idênticos na mesma volta virem registros distintos.
        """
        tupla = self._checkpointer.get_tuple(self._config(thread_id))
        if tupla is None:
            return 1
        valores = tupla.checkpoint.get("channel_values", {})
        return int(valores.get("passo", 0)) + 1

    def _escritor(self) -> Escritor | None:
        """O que o nó `escrever` chama depois do aprovado humano.

        `None` quando o CRM não está configurado: o grafo então diz que não
        gravou, em vez de fingir que gravou. Ver `nos.criar_escrever`.
        """
        cliente = self._cliente_crm
        if cliente is None:
            return None

        def escrever(proposta: PropostaDeRegistro) -> SaidaRegistrarCrm:
            return enviar_registro(proposta, cliente=cliente, base_url=self._crm_base_url)

        return escrever

    def _grafo(self, thread_id: str) -> Any:
        passo = self._passo_seguinte(thread_id)
        registro = montar_registro(
            recuperador=self._recuperador,
            catalogo=self._catalogo,
            parametros=self._parametros_tools,
            thread_id=thread_id,
            passo_atual=lambda: passo,
        )
        return compilar(
            Dependencias(
                provedor=self._provedor,
                registro=registro,
                parametros=self._parametros_grafo,
                escrever=self._escritor(),
            ),
            checkpointer=self._checkpointer,
        )

    # --- os verbos que `rotas.Copiloto` declara ------------------------------

    def nova_thread(self) -> str:
        """Identificador de conversa. O hexa do `uuid4` cabe no `PADRAO_THREAD`."""
        return uuid.uuid4().hex

    def responder(self, pergunta: str, *, thread_id: str) -> SaidaConsulta:
        with self._trava:
            grafo = self._grafo(thread_id)
            final = grafo.invoke(
                {"pergunta": pergunta, "thread_id": thread_id}, self._config(thread_id)
            )
        return _saida_consulta(thread_id, final)

    def decidir(self, decisao: Decisao) -> SaidaDecisao:
        with self._trava:
            configuracao = self._config(decisao.thread_id)
            if self._checkpointer.get_tuple(configuracao) is None:
                raise ConsultaNaoEncontrada(decisao.thread_id)
            grafo = self._grafo(decisao.thread_id)
            if not _tem_aprovacao_pendente(grafo.get_state(configuracao)):
                raise NadaAAprovar(decisao.thread_id)
            final = grafo.invoke(
                Command(
                    resume={
                        "aprovado": decisao.aprovado,
                        "revisor": decisao.revisor,
                        "observacao": decisao.observacao,
                    }
                ),
                configuracao,
            )
        return _saida_decisao(decisao.thread_id, final)

    def saude(self) -> SaidaSaude:
        """Estado local, sem tocar a rede. Um `/saude` que chama a Groq mede a Groq."""
        try:
            linha = self._catalogo.execute(
                "SELECT count(*) AS total,"
                " sum(status_vigencia = 'revogada') AS revogadas FROM normas"
            ).fetchone()
            catalogo: dict[str, Any] = {
                "normas": int(linha["total"]),
                "revogadas": int(linha["revogadas"] or 0),
            }
        except sqlite3.Error as erro:
            logger.error("catálogo indisponível", extra={"erro": str(erro)})
            catalogo = {"normas": 0, "erro": "catalogo_indisponivel"}

        componentes: dict[str, Any] = {
            "provedor": {"id_sistema": self._provedor.id_sistema},
            "catalogo": catalogo,
            "checkpoint": {
                "arquivo": str(self._caminho_checkpoint),
                "existe": self._caminho_checkpoint.exists(),
            },
            # Sem chamada de rede: o que se afirma é que existe destino
            # configurado, não que ele está no ar. Afirmar disponibilidade
            # exigiria bater lá — e aí `/saude` passaria a medir o CRM.
            "crm": {"configurado": self._cliente_crm is not None, "base_url": self._crm_base_url},
        }
        degradado = catalogo["normas"] == 0 or not componentes["crm"]["configurado"]
        return SaidaSaude(
            status="degradado" if degradado else "ok",
            versao=versao_do_pacote(),
            componentes=componentes,
        )


def _tem_aprovacao_pendente(instantaneo: Any) -> bool:
    """O grafo está parado num `interrupt()`?

    A pergunta é feita às tarefas pendentes do checkpoint, não a um campo do
    estado: quem sabe que há interrupção viva é o LangGraph, e manter uma flag
    paralela seria manter duas verdades sobre a mesma coisa.
    """
    return any(getattr(tarefa, "interrupts", ()) for tarefa in getattr(instantaneo, "tasks", ()))


def _saida_consulta(thread_id: str, final: Mapping[str, Any]) -> SaidaConsulta:
    interrupcoes = final.get("__interrupt__") or ()
    if interrupcoes:
        pedido = getattr(interrupcoes[0], "value", {}) or {}
        return SaidaConsulta(
            thread_id=thread_id,
            estado="aguardando_aprovacao",
            proposta=pedido.get("proposta"),
            achados_pii=list(final.get("achados_pii", [])),
            passos=int(final.get("passo", 0)),
        )
    return SaidaConsulta(
        thread_id=thread_id,
        estado=final.get("encerramento") or "respondida",
        resposta=final.get("resposta", ""),
        citacoes=list(final.get("citacoes", [])),
        achados_pii=list(final.get("achados_pii", [])),
        passos=int(final.get("passo", 0)),
        motivo=final.get("motivo", ""),
    )


def _saida_decisao(thread_id: str, final: Mapping[str, Any]) -> SaidaDecisao:
    interrupcoes = final.get("__interrupt__") or ()
    if interrupcoes:
        # O agente propôs outra escrita depois de resolvida a primeira. Continua
        # sendo decisão humana: a API devolve o mesmo estado de espera.
        return SaidaDecisao(
            thread_id=thread_id,
            estado="aguardando_aprovacao",
            registro=final.get("registro"),
            passos=int(final.get("passo", 0)),
        )
    return SaidaDecisao(
        thread_id=thread_id,
        estado=final.get("encerramento") or "respondida",
        resposta=final.get("resposta", ""),
        citacoes=list(final.get("citacoes", [])),
        registro=final.get("registro"),
        passos=int(final.get("passo", 0)),
        motivo=final.get("motivo", ""),
    )


def criar_app(
    copiloto: Copiloto | None = None, *, configuracao: Configuracao | None = None
) -> FastAPI:
    """O app do FastAPI.

    `copiloto` pronto entra por parâmetro (é o que o teste injeta) ou é aberto no
    `lifespan` a partir da configuração. Abrir no `lifespan`, e não no import, é o
    que mantém `import copiloto.api.main` barato: importar o módulo não carrega
    modelo, não abre índice e não exige `GROQ_API_KEY`.
    """

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        proprio = copiloto is None
        servico = copiloto or Servico.abrir(configuracao or Configuracao.do_ambiente())
        app.state.copiloto = servico
        logger.info("api pronta", extra={"versao": versao_do_pacote()})
        try:
            yield
        finally:
            if proprio and isinstance(servico, Servico):
                servico.fechar()

    app = FastAPI(
        title="Copiloto Normativo BCB",
        version=versao_do_pacote(),
        summary=(
            "Responde perguntas sobre normas do BCB citando norma, artigo e status de "
            "vigência. Não interpreta a norma e não afirma conformidade."
        ),
        lifespan=ciclo_de_vida,
    )
    app.include_router(criar_router())
    registrar_tratadores(app)
    return app
