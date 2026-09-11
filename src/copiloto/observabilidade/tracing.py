"""O trace do Langfuse — latência por nó, tokens por chamada e a etiqueta que a Fase 9 lê.

**Observabilidade nunca derruba o copiloto.** Se o pacote `langfuse` não está
instalado, ou as chaves não estão no ambiente, ou o serviço caiu, o que sobe é o
`RastreadorNulo`: mesma superfície, nenhum efeito. Um sistema que para de
responder porque o coletor de métricas ficou indisponível trocou o objetivo pela
instrumentação. Por isso o `langfuse` é extra (`pip install -e ".[obs]"`) e não
dependência principal — e por isso a suíte hermética do CI não o instala.

**As duas linhas que a Fase 9 consome.** Todo trace carrega `id_sistema` (qual
dos provedores do §3.2 atendeu) e `versao_ficha` (a versão de
`config/governanca.yaml`). Emitir agora custa este módulo; emitir depois exigiria
reprocessar trace já gravado, que é o mesmo que não ter. Enquanto a ficha da Fase
9 não existe, o valor é `nao-declarada` — declarar ausência é dado, inventar
`1.0.0` não é.

**A pergunta que entra no trace é a saneada.** O `interrupt()` e o CPF do §8 são
o motivo: o guardrail de entrada roda dentro do grafo e reescreve `pergunta` no
estado, então a `Sessao` só registra o texto *depois* da execução, lendo o estado
final. Abrir o span com a pergunta crua mandaria dado pessoal para um terceiro
exatamente no caminho que o guardrail existe para fechar.

**Sobre o custo que o Langfuse mostra.** Ele é derivado do modelo mais os tokens,
pela tabela de preços do próprio Langfuse. No free tier da Groq o custo real é
R$ 0; o número do painel é o equivalente em preço de lista, e é assim que ele
deve ser lido — ver §16.6.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from copiloto.llm.provedor import (
    EspecificacaoDeTool,
    Mensagem,
    ProvedorLLM,
    RespostaLLM,
)

logger = logging.getLogger(__name__)

RAIZ_PADRAO = Path(__file__).resolve().parents[3]

# Enquanto `config/governanca.yaml` não existe (ele nasce na Fase 9), é este o
# valor que viaja no trace. É afirmação verdadeira: não há ficha declarada.
FICHA_NAO_DECLARADA = "nao-declarada"

# Os tipos de observação que o Langfuse conhece e que este sistema usa. Não é
# enfeite de UI: o tipo é o que faz o `generation` carregar custo, o que deixa
# um avaliador LLM-as-a-judge filtrar só as chamadas de tool, e o que monta o
# agent graph. Marcar tudo como `span` joga fora as três coisas.
TipoDeObservacao = Literal["span", "agent", "tool", "chain", "retriever", "evaluator", "guardrail"]

# `identificacao: { versao: X }`, lido por varredura de linha e não por parser de
# YAML de propósito: o `pyyaml` não está na lista fechada do §3, e o único dado
# que este módulo precisa do arquivo é uma string. Trazer um parser inteiro para
# ler um campo seria dependência nova sem necessidade.
_VERSAO = re.compile(r"^\s{2,}versao\s*:\s*['\"]?([^'\"#\s]+)")
_IDENTIFICACAO = re.compile(r"^identificacao\s*:")


def versao_da_ficha(raiz: Path = RAIZ_PADRAO) -> str:
    """A versão declarada em `config/governanca.yaml`, ou `nao-declarada`."""
    caminho = raiz / "config" / "governanca.yaml"
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError:
        return FICHA_NAO_DECLARADA

    dentro = False
    for linha in linhas:
        if _IDENTIFICACAO.match(linha):
            dentro = True
            continue
        if dentro:
            achado = _VERSAO.match(linha)
            if achado:
                return achado.group(1)
            # Saiu do bloco `identificacao` sem achar `versao`.
            if linha.strip() and not linha.startswith((" ", "\t")):
                break
    return FICHA_NAO_DECLARADA


# --- a superfície que o resto do sistema enxerga -----------------------------


class Observacao(Protocol):
    """Um span aberto. `atualizar` aceita o que o backend souber usar."""

    def atualizar(self, **campos: Any) -> None: ...


class Sessao(Protocol):
    """Uma execução do grafo, do ponto de vista do trace.

    `registrar` é chamado no fim, com o estado final: é o único momento em que a
    pergunta já passou pelo guardrail de entrada.
    """

    def no(self, nome: str, *, tipo: TipoDeObservacao = "span") -> Any: ...

    def geracao(self, *, nome: str, modelo: str, id_sistema: str) -> Any: ...

    def registrar(self, **campos: Any) -> None: ...

    def id_do_trace(self) -> str: ...


class Rastreador(Protocol):
    """Abre sessões e garante a entrega. `ativo` diz se algo sai daqui.

    `ativo` é propriedade só de leitura no contrato: quem implementa pode
    satisfazê-la com atributo de classe, campo de dataclass ou property, e
    ninguém de fora liga o trace escrevendo nesta flag.
    """

    @property
    def ativo(self) -> bool: ...

    def sessao(self, *, nome: str, thread_id: str, id_sistema: str) -> Any: ...

    def descarregar(self) -> None: ...


# --- implementação nula ------------------------------------------------------


class _ObservacaoNula:
    __slots__ = ()

    def atualizar(self, **campos: Any) -> None:
        return None


class _SessaoNula:
    __slots__ = ()

    @contextmanager
    def no(
        self, nome: str, *, tipo: TipoDeObservacao = "span"
    ) -> Generator[_ObservacaoNula, None, None]:
        yield _ObservacaoNula()

    @contextmanager
    def geracao(
        self, *, nome: str, modelo: str, id_sistema: str
    ) -> Generator[_ObservacaoNula, None, None]:
        yield _ObservacaoNula()

    def registrar(self, **campos: Any) -> None:
        return None

    def id_do_trace(self) -> str:
        return ""


def sessao_nula() -> Sessao:
    """A sessão que não registra nada.

    É o padrão de `Dependencias` no grafo: montar um grafo sem falar de trace
    tem de continuar sendo possível, e é o que a maior parte dos testes faz.
    """
    return _SessaoNula()


@dataclass(frozen=True, slots=True)
class RastreadorNulo:
    """Sem Langfuse. Toda chamada é válida e não faz nada.

    Não é stub de teste: é o modo normal de operação de qualquer ambiente sem
    chaves — o dev sem conta, o CI, o container da Fase 8 antes de configurar o
    segredo. Por isso mora no código de produção e é testado.
    """

    motivo: str = ""
    ativo: bool = False

    @contextmanager
    def sessao(
        self, *, nome: str, thread_id: str, id_sistema: str
    ) -> Generator[_SessaoNula, None, None]:
        yield _SessaoNula()

    def descarregar(self) -> None:
        return None


# --- implementação Langfuse --------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Observacao:
    """Adapta o span do Langfuse à superfície mínima daqui."""

    span: Any

    def atualizar(self, **campos: Any) -> None:
        try:
            self.span.update(**campos)
        except Exception:  # noqa: BLE001 — trace quebrado não pode quebrar a consulta
            logger.warning("falha ao atualizar observação no trace", exc_info=False)


@dataclass(frozen=True, slots=True)
class _SessaoLangfuse:
    cliente: Any
    raiz: Any

    @contextmanager
    def no(
        self, nome: str, *, tipo: TipoDeObservacao = "span"
    ) -> Generator[_Observacao, None, None]:
        """Uma observação por passo. É daqui que sai a latência por nó e por tool."""
        try:
            gerenciador = self.cliente.start_as_current_observation(name=nome, as_type=tipo)
        except Exception:  # noqa: BLE001
            logger.warning("falha ao abrir span do nó %s", nome)
            yield _Observacao(span=None)
            return
        with gerenciador as span:
            yield _Observacao(span=span)

    @contextmanager
    def geracao(
        self, *, nome: str, modelo: str, id_sistema: str
    ) -> Generator[_Observacao, None, None]:
        """Uma chamada de LLM. É daqui que saem tokens e, por consequência, custo."""
        try:
            gerenciador = self.cliente.start_as_current_observation(
                name=nome,
                as_type="generation",
                model=modelo,
                metadata={"id_sistema": id_sistema},
            )
        except Exception:  # noqa: BLE001
            logger.warning("falha ao abrir geração no trace")
            yield _Observacao(span=None)
            return
        with gerenciador as span:
            yield _Observacao(span=span)

    def registrar(self, **campos: Any) -> None:
        """Entrada e saída do trace inteiro, já saneadas pelo guardrail."""
        if self.raiz is None:
            return
        try:
            self.raiz.update(**campos)
        except Exception:  # noqa: BLE001
            logger.warning("falha ao registrar entrada/saída no trace")

    def id_do_trace(self) -> str:
        """O id do trace desta execução, para a resposta HTTP poder citá-lo.

        Sem isto, achar no painel o trace de uma consulta que deu errado exige
        garimpar por horário — e é exatamente na hora do incidente que ninguém
        tem tempo de garimpar.
        """
        try:
            return str(self.cliente.get_current_trace_id() or "")
        except Exception:  # noqa: BLE001
            logger.warning("falha ao obter o id do trace")
            return ""


class RastreadorLangfuse:
    """O rastreador de verdade. Construir por `abrir_rastreador`, não direto.

    O `import` do `langfuse` acontece em `abrir_rastreador` e o cliente chega
    pronto: assim este módulo continua importável — e testável — numa máquina que
    nunca instalou o extra `[obs]`.
    """

    ativo = True

    def __init__(self, cliente: Any, *, versao_ficha: str, propagar: Any) -> None:
        self._cliente = cliente
        self._versao_ficha = versao_ficha
        self._propagar = propagar

    @contextmanager
    def sessao(
        self, *, nome: str, thread_id: str, id_sistema: str
    ) -> Generator[Sessao, None, None]:
        """Abre o trace da execução, já etiquetado.

        `id_sistema` e `versao_ficha` entram por `propagate_attributes`, que os
        carimba no trace inteiro e não só no span raiz — é o que permite à Fase 9
        agregar custo por sistema sem varrer span a span.
        """
        atributos = {
            "session_id": thread_id,
            "trace_name": nome,
            "metadata": {"id_sistema": id_sistema, "versao_ficha": self._versao_ficha},
            "tags": [f"id_sistema:{id_sistema}", f"versao_ficha:{self._versao_ficha}"],
        }
        # **O `try` cobre só a abertura, nunca o corpo do `with`.** Envolver o
        # `yield` num `except Exception` parece defensivo e é um bug: o contextlib
        # *lança* a exceção do corpo de volta dentro do gerador, o `except` a
        # engole, o gerador cede uma segunda vez e o Python levanta
        # `RuntimeError: generator didn't stop after throw()` — trocando o erro
        # real do copiloto por um erro do instrumentador. Foi exatamente o que
        # aconteceu ao gerar o primeiro trace desta fase: um HTTP 413 da Groq
        # chegou ao usuário disfarçado de defeito do tracing.
        try:
            gerenciadores = (
                self._propagar(**atributos),
                self._cliente.start_as_current_observation(name=nome, as_type="span"),
            )
        except Exception:  # noqa: BLE001 — ver docstring do módulo
            logger.warning("trace indisponível nesta execução", exc_info=False)
            yield _SessaoNula()
            return

        propagacao, observacao = gerenciadores
        with propagacao, observacao as raiz:
            yield _SessaoLangfuse(cliente=self._cliente, raiz=raiz)

    def descarregar(self) -> None:
        """Entrega o que está na fila. Processo curto morre antes do envio sem isto."""
        try:
            self._cliente.flush()
        except Exception:  # noqa: BLE001
            logger.warning("falha ao descarregar o trace")


def abrir_rastreador(
    ambiente: Mapping[str, str] | None = None,
    *,
    raiz: Path = RAIZ_PADRAO,
    release: str = "",
) -> Rastreador:
    """O rastreador que este ambiente permite. Nunca levanta exceção.

    Quatro motivos para cair no `RastreadorNulo`, e todos são operação normal: o
    extra `[obs]` não instalado, as chaves ausentes, o cliente falhando na
    construção, ou as credenciais recusadas. O motivo fica registrado no objeto
    para o `/saude` dizer *por que* não há trace, em vez de só dizer que não há.

    **Por que há uma chamada de rede aqui.** O SDK não autentica ao construir: com
    chave errada ele sobe normalmente, engole os spans e não reclama. Sem o
    `auth_check`, `/saude` responderia `observabilidade.ativo = true` num serviço
    que não exporta nada — e o modo de falha de observabilidade que interessa não
    é o barulhento, é esse. A chamada acontece uma vez, no boot do serviço, e
    falhar nela não impede o copiloto de responder.
    """
    amb = ambiente if ambiente is not None else os.environ
    publica = amb.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secreta = amb.get("LANGFUSE_SECRET_KEY", "").strip()
    if not publica or not secreta:
        return RastreadorNulo(motivo="chaves_ausentes")

    try:
        from langfuse import Langfuse, propagate_attributes
    except ImportError:
        # Chave configurada e pacote ausente é engano de instalação, não escolha:
        # avisa alto, mas segue respondendo.
        logger.warning("LANGFUSE_* definido mas o pacote não está instalado; use .[obs]")
        return RastreadorNulo(motivo="pacote_ausente")

    # `LANGFUSE_HOST` é o nome que o `.env` do projeto usa desde a Fase 0 e o que
    # o SDK v3 lia; o v4 passou a chamá-lo `LANGFUSE_BASE_URL`. Aceitar os dois
    # evita que uma renomeação no SDK apague silenciosamente o destino. No
    # construtor, é `base_url=` que recebe o valor: `host=` continua aceito no
    # v4, mas está marcado `deprecated` no SDK a favor de `base_url`.
    base = amb.get("LANGFUSE_HOST", "").strip() or amb.get("LANGFUSE_BASE_URL", "").strip()
    try:
        cliente = Langfuse(
            public_key=publica,
            secret_key=secreta,
            base_url=base or None,
            environment=amb.get("LANGFUSE_ENVIRONMENT", "").strip() or None,
            release=release or None,
            timeout=int(amb.get("LANGFUSE_TIMEOUT", "10") or 10),
        )
    except Exception as erro:  # noqa: BLE001
        logger.warning("cliente do langfuse não subiu", extra={"erro": str(erro)})
        return RastreadorNulo(motivo="cliente_indisponivel")

    return autenticar(cliente, propagate_attributes, raiz=raiz, host=base)


def autenticar(
    cliente: Any, propagar: Any, *, raiz: Path = RAIZ_PADRAO, host: str = ""
) -> Rastreador:
    """Confere as credenciais e devolve o rastreador correspondente.

    Separado de `abrir_rastreador` para ser testável sem o extra `[obs]`
    instalado: o CI não tem o `langfuse`, e este é justamente o caminho que
    precisa de teste — chave errada tem de virar `RastreadorNulo` com motivo, e
    não um rastreador que se diz ativo e descarta tudo em silêncio.
    """
    try:
        autenticado = bool(cliente.auth_check())
    except Exception as erro:  # noqa: BLE001
        logger.warning(
            "credenciais do langfuse recusadas; seguindo sem trace",
            extra={"host": host, "erro": type(erro).__name__},
        )
        return RastreadorNulo(motivo="credenciais_invalidas")
    if not autenticado:
        logger.warning("langfuse não autenticou; seguindo sem trace", extra={"host": host})
        return RastreadorNulo(motivo="credenciais_invalidas")

    return RastreadorLangfuse(cliente, versao_ficha=versao_da_ficha(raiz), propagar=propagar)


# --- o provedor instrumentado ------------------------------------------------


def mensagem_para_openai(mensagem: Mensagem) -> dict[str, Any]:
    """`Mensagem` no formato de mensagem da OpenAI, que é o que o painel renderiza.

    **Por que chaves em inglês num projeto com nomes de domínio em português.**
    Isto não é nome de domínio: é formato de fio. O Langfuse só desenha a conversa
    como diálogo — com papéis, e com as chamadas de tool viradas cartão — quando
    encontra `role`, `content` e `tool_calls` com `arguments` em JSON string. Com
    `papel`/`conteudo` ele mostra um blob de JSON cru, e a razão de olhar um trace
    é justamente ler o que o modelo viu sem decifrar blob.

    Mesma justificativa das chaves de `usage_details` na geração.

    **Esta função é gêmea da de `llm/groq.py` e não deve ser fundida com ela.**
    Elas são idênticas hoje e respondem a contratos externos diferentes: a de lá
    monta o corpo da requisição da Groq, a daqui monta o que o painel do Langfuse
    sabe desenhar. Unificá-las criaria um acoplamento em que mudar o formato de
    renderização do trace mexe no que se manda para o provedor de LLM — e o dia
    em que um dos dois lados mudar, a "deduplicação" vira bug em produção.
    """
    corpo: dict[str, Any] = {"role": mensagem.papel, "content": mensagem.conteudo}
    if mensagem.chamadas:
        corpo["tool_calls"] = [
            {
                "id": chamada.id,
                "type": "function",
                "function": {
                    "name": chamada.nome,
                    # String, não objeto: é assim que a API da OpenAI transmite, e
                    # é o formato em que o painel reconhece a chamada.
                    "arguments": json.dumps(chamada.argumentos, ensure_ascii=False),
                },
            }
            for chamada in mensagem.chamadas
        ]
    if mensagem.id_chamada:
        corpo["tool_call_id"] = mensagem.id_chamada
    return corpo


@dataclass(slots=True)
class ProvedorRastreado:
    """Decora um `ProvedorLLM` e emite uma geração por chamada.

    **Por que decorador e não `langfuse` dentro de `groq.py`.** A dependência
    aponta numa direção só: `observabilidade` conhece `llm`, `llm` não conhece
    `observabilidade`. Sem isso, cada provedor novo do §3.2 nasceria com o
    Langfuse costurado dentro e o teste do provedor passaria a exigir o extra
    `[obs]` instalado.

    Cumpre `ProvedorLLM` por estrutura — inclusive `id_sistema` —, então o grafo
    não tem como saber que está falando com um decorador.
    """

    provedor: ProvedorLLM
    sessao: Sessao = field(default_factory=_SessaoNula)
    id_sistema: str = field(init=False)

    def __post_init__(self) -> None:
        self.id_sistema = self.provedor.id_sistema

    def gerar(
        self,
        mensagens: Sequence[Mensagem],
        *,
        tools: Sequence[EspecificacaoDeTool] = (),
        temperatura: float | None = None,
    ) -> RespostaLLM:
        with self.sessao.geracao(
            nome="llm.gerar", modelo="", id_sistema=self.id_sistema
        ) as observacao:
            observacao.atualizar(
                input=[mensagem_para_openai(m) for m in mensagens],
                model_parameters={"temperature": temperatura},
                metadata={"tools_ofertadas": [t.nome for t in tools]},
            )
            resposta = self.provedor.gerar(mensagens, tools=tools, temperatura=temperatura)
            observacao.atualizar(
                model=resposta.modelo,
                output=mensagem_para_openai(
                    Mensagem.assistente(resposta.conteudo, resposta.chamadas)
                ),
                # Os nomes das chaves são os que o Langfuse entende para casar com
                # a tabela de preços e derivar custo. Trocar por `tokens_entrada`
                # deixaria o painel com token e sem custo.
                usage_details={
                    "input": resposta.uso.tokens_entrada,
                    "output": resposta.uso.tokens_saida,
                    "total": resposta.uso.tokens_entrada + resposta.uso.tokens_saida,
                },
                metadata={
                    "id_sistema": resposta.id_sistema or self.id_sistema,
                    "motivo_parada": resposta.motivo_parada,
                },
            )
            return resposta
