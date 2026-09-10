"""Os três endpoints e os contratos que eles publicam.

Este módulo é a fronteira HTTP e nada além disso: valida forma, traduz erro de
domínio em código de status e devolve JSON. Quem sabe montar grafo, abrir índice
e falar com o CRM é `main.py`.

**Por que um `Protocol` e não o serviço concreto.** As rotas dependem de três
verbos — responder, decidir, saúde. Depender da classe traria junto Chroma,
FlashRank e o provedor de LLM para dentro do teste de rota, e o que se quer medir
aqui é status HTTP e formato, não recuperação. `Copiloto` é a interface; `main.py`
é uma implementação, o duplo do teste é outra.

**Por que a interrupção é 200 e não 202.** `POST /perguntar` responde 200 com
`estado = "aguardando_aprovacao"` quando o grafo parou no `interrupt()`. 202 diria
"aceito, processando depois" — e não há processamento em andamento: o grafo está
parado à espera de decisão humana, e a resposta já carrega a proposta a ser
aprovada. Cliente nenhum precisa fazer polling; ele precisa ler `estado`.

**O que a API não valida.** Injeção de prompt e PII não são checadas aqui. O
guardrail de entrada (`grafo/guardrails.py`) é a autoridade sobre o conteúdo, e
ele roda dentro do grafo, onde o resultado fica gravado no checkpoint e no trace.
A API confere só forma: tamanho, tipo e padrão de identificador.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from copiloto.llm.provedor import ErroDeProvedor
from copiloto.tools.schemas import PADRAO_THREAD

logger = logging.getLogger(__name__)

# Os encerramentos de `EstadoDoAgente` mais o estado que só existe na API: o grafo
# parado no `interrupt()` não encerrou, mas a requisição HTTP terminou.
Estado = Literal[
    "respondida",
    "aguardando_aprovacao",
    "bloqueada_na_entrada",
    "sem_base_normativa",
    "limite_de_passos",
    "falha_de_tool",
]


class Corpo(BaseModel):
    """Base dos contratos HTTP: fechado, como os das tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- entrada -----------------------------------------------------------------


class Pergunta(Corpo):
    """O que entra em `POST /perguntar`."""

    pergunta: Annotated[str, Field(min_length=5, max_length=500)]
    # Sem `thread_id` a API cria um: conversa nova. Com ele, o pedido continua a
    # conversa que já está no checkpoint — é assim que o n8n reusa o contexto.
    thread_id: Annotated[str, Field(pattern=PADRAO_THREAD)] | None = None


class Decisao(Corpo):
    """O que entra em `POST /aprovar`. `revisor` é obrigatório de propósito.

    Aprovação anônima não é aprovação humana: sem nome no registro, a autonomia
    nível 2 vira nível 3 com etapa extra. Quem aprovou fica no estado e viaja
    para o trace.
    """

    thread_id: Annotated[str, Field(pattern=PADRAO_THREAD)]
    aprovado: bool
    revisor: Annotated[str, Field(min_length=2, max_length=80)]
    observacao: Annotated[str, Field(max_length=500)] = ""


# --- saída -------------------------------------------------------------------


class SaidaConsulta(Corpo):
    """A resposta de `POST /perguntar`.

    `trace_id` é vazio quando não há observabilidade configurada — que é operação
    normal, não erro. Quando há, ele é o que liga esta resposta ao trace do
    painel: sem ele, achar o trace de uma consulta específica vira garimpo por
    horário, justamente na hora em que ninguém tem tempo de garimpar.
    """

    thread_id: str
    trace_id: str = ""
    estado: Estado
    resposta: str = ""
    citacoes: list[str] = Field(default_factory=list)
    proposta: dict[str, Any] | None = None
    achados_pii: list[str] = Field(default_factory=list)
    passos: int = 0
    motivo: str = ""


class SaidaDecisao(Corpo):
    """A resposta de `POST /aprovar`. Trace próprio, mesma conversa."""

    thread_id: str
    trace_id: str = ""
    estado: Estado
    resposta: str = ""
    citacoes: list[str] = Field(default_factory=list)
    registro: dict[str, Any] | None = None
    passos: int = 0
    motivo: str = ""


class SaidaSaude(Corpo):
    """A resposta de `GET /saude`.

    Nunca devolve 5xx. Um `/saude` que responde 503 porque o CRM não está
    configurado faz o orquestrador derrubar um container que atende perfeitamente
    a `/perguntar` — a saúde do processo e a disponibilidade das integrações são
    coisas diferentes, e por isso vêm em campos diferentes.
    """

    status: Literal["ok", "degradado"]
    versao: str
    componentes: dict[str, Any] = Field(default_factory=dict)


# --- erros de domínio da API -------------------------------------------------


class ErroDeApi(RuntimeError):
    """Erro que vira status HTTP. Subclasse define o código."""

    status = 500
    codigo = "erro_interno"


class ConsultaNaoEncontrada(ErroDeApi):
    """`thread_id` sem checkpoint: ninguém perguntou nessa conversa."""

    status = 404
    codigo = "consulta_inexistente"


class NadaAAprovar(ErroDeApi):
    """A conversa existe, mas não está parada esperando decisão humana."""

    status = 409
    codigo = "sem_aprovacao_pendente"


# --- interface que as rotas consomem -----------------------------------------


class Copiloto(Protocol):
    """Os três verbos. Implementado por `main.Servico` e pelos duplos de teste."""

    def responder(self, pergunta: str, *, thread_id: str) -> SaidaConsulta: ...

    def decidir(self, decisao: Decisao) -> SaidaDecisao: ...

    def saude(self) -> SaidaSaude: ...

    def nova_thread(self) -> str: ...


def copiloto_de(request: Request) -> Copiloto:
    servico: Copiloto = request.app.state.copiloto
    return servico


# --- rotas -------------------------------------------------------------------


def criar_router() -> APIRouter:
    """As rotas. Endpoint síncrono: o grafo é síncrono e o uvicorn o põe em worker."""
    router = APIRouter()

    @router.post("/perguntar", response_model=SaidaConsulta)
    def perguntar(pedido: Pergunta, request: Request) -> SaidaConsulta:
        copiloto = copiloto_de(request)
        thread_id = pedido.thread_id or copiloto.nova_thread()
        logger.info("consulta recebida", extra={"thread_id": thread_id})
        return copiloto.responder(pedido.pergunta, thread_id=thread_id)

    @router.post("/aprovar", response_model=SaidaDecisao)
    def aprovar(decisao: Decisao, request: Request) -> SaidaDecisao:
        logger.info(
            "decisão humana recebida",
            extra={"thread_id": decisao.thread_id, "aprovado": decisao.aprovado},
        )
        return copiloto_de(request).decidir(decisao)

    @router.get("/saude", response_model=SaidaSaude)
    def saude(request: Request) -> SaidaSaude:
        return copiloto_de(request).saude()

    return router


def registrar_tratadores(app: FastAPI) -> None:
    """Erro de domínio vira status; falha de provedor vira 503, não 500.

    A distinção importa para quem opera: 503 é "a Groq caiu, tente de novo"; 500
    seria "há um bug aqui". Trocar um pelo outro manda o plantão para o lugar
    errado.
    """

    @app.exception_handler(ErroDeApi)
    def _erro_de_api(_: Request, erro: ErroDeApi) -> JSONResponse:
        return JSONResponse(
            status_code=erro.status, content={"erro": erro.codigo, "detalhe": str(erro)}
        )

    @app.exception_handler(ErroDeProvedor)
    def _erro_de_provedor(_: Request, erro: ErroDeProvedor) -> JSONResponse:
        logger.error("provedor de llm indisponível", extra={"erro": str(erro)})
        return JSONResponse(
            status_code=503, content={"erro": "provedor_indisponivel", "detalhe": str(erro)}
        )
