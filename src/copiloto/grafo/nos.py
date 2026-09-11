"""Os nós do grafo. Cada um é função de estado para atualização parcial de estado.

**Nenhum nó chama outro nó.** Quem decide caminho é a aresta condicional, em
`grafo.py`; aqui só se decide *o que registrar no estado*. Por isso as funções de
rumo (`rumo_apos_*`) também moram neste módulo: elas leem estado e devolvem o
nome do próximo destino, sem executar nada.

**Autonomia nível 2.** Existe exatamente um ponto de escrita externa (`escrever`)
e ele só é alcançável pela aresta que sai de `aprovar`, que por sua vez só
devolve controle depois do `interrupt()`. Não há caminho de código em que o
modelo alcance o CRM sozinho — a tool que ele enxerga produz uma *proposta*.

**Dependência entra por parâmetro.** Provedor, registro de tools e escritor do
CRM chegam em `Dependencias`; o estado guarda só dado serializável. Estado com
conexão dentro não atravessa um reinício de processo, e atravessar é o critério
de pronto da fase.
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from copiloto.grafo.estado import EstadoDoAgente, gravar, transcricao
from copiloto.grafo.guardrails import SEM_BASE_NORMATIVA, sanear_pergunta, validar_resposta
from copiloto.llm.provedor import Mensagem, ProvedorLLM
from copiloto.observabilidade.tracing import Sessao, sessao_nula
from copiloto.tools.buscar_normativo import NOME as TOOL_BUSCAR
from copiloto.tools.consultar_catalogo import NOME as TOOL_CATALOGO
from copiloto.tools.registrar_crm import NOME as TOOL_CRM
from copiloto.tools.registrar_crm import ErroDeCrm
from copiloto.tools.registro import ToolRegistrada, executar_com_autocorrecao
from copiloto.tools.schemas import (
    PropostaDeRegistro,
    SaidaBuscarNormativo,
    SaidaRegistrarCrm,
    TrechoCitado,
)

logger = logging.getLogger(__name__)

# O prompt orienta; ele não garante. Tudo que pode ser conferido por código está
# em `guardrails.py`, e o que está escrito aqui é o que não dá para verificar:
# a divisão de trabalho entre as tools e o tom da resposta.
#
# **Os nomes das tools vêm das próprias tools, e isso custou um defeito para ser
# aprendido.** Escritos à mão, este prompt mandava o modelo chamar
# `consultar_catalogo_normas` e `registrar_consulta_crm`, que não existem no
# registro: toda pergunta de metadado gastava uma volta inteira do grafo em
# `tool_desconhecida` antes de a autocorreção acertar o nome. Nenhum teste pegou,
# porque os testes montam registro próprio. Agora o nome é o mesmo objeto nos dois
# lados, e `evals/rodar.py` verifica a correspondência a cada push.
PROMPT_SISTEMA = (
    "Você é um copiloto normativo do Banco Central do Brasil. Responde localizando a norma, "
    "o artigo e o status de vigência, sempre citando a fonte.\n"
    f"- Use {TOOL_BUSCAR} para perguntas sobre o conteúdo das normas.\n"
    f"- Use {TOOL_CATALOGO} para perguntas de metadado (quais normas, de que ano, "
    "quais estão vigentes). Metadado é consulta exata, não busca por similaridade.\n"
    f"- Se você já sabe em qual norma procurar — porque a pergunta a nomeia ou porque "
    f"{TOOL_CATALOGO} acabou de devolvê-la — passe o número dela em `numero` para "
    f"{TOOL_BUSCAR}. Buscar dentro de uma norma é mais preciso que buscar em todas.\n"
    f"- Use {TOOL_CRM} apenas quando o usuário pedir registro da consulta. "
    "A gravação depende de aprovação humana.\n"
    "- Cite exatamente como a tool devolveu, no formato "
    "'Resolução BCB nº 85, de 2021, art. 7º'. Toda citação é conferida por código contra os "
    "trechos recuperados nesta execução.\n"
    "- Se a norma citada estiver revogada, diga isso explicitamente.\n"
    "- Não interprete a norma, não emita parecer jurídico e não afirme conformidade.\n"
    f"- Sem trecho que sustente a resposta, responda exatamente: {SEM_BASE_NORMATIVA}"
)


@dataclass(frozen=True, slots=True)
class ParametrosDoGrafo:
    """A seção `[grafo]` do TOML mais a temperatura, que é de `[llm]`.

    São limite de segurança, não sugestão: estourou, o grafo termina com fallback
    explícito. Mudar o número no TOML muda o comportamento sem tocar em Python.
    """

    max_passos: int
    max_autocorrecao: int
    max_tentativas_resposta: int
    temperatura: float | None = None


def carregar_parametros(caminho: Path) -> ParametrosDoGrafo:
    """Lê `config/parametros.toml`. Chave a mais ou a menos falha aqui."""
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    return ParametrosDoGrafo(**dados["grafo"], temperatura=dados["llm"]["temperatura"])


@dataclass(frozen=True, slots=True)
class Dependencias:
    """O que os nós precisam e o estado não pode carregar.

    `escrever` é `None` por padrão de propósito: um ambiente que não configurou o
    CRM não escreve, e diz que não escreveu. O padrão silencioso seria escrever.

    `sessao` é a da Fase 7 e é dependência pela mesma razão que o provedor: ela
    vale por execução (carrega o `thread_id` no trace) e não é serializável, então
    não pode morar no estado. O padrão é a sessão nula — montar grafo sem falar de
    observabilidade continua sendo possível, que é o que quase todo teste faz.
    """

    provedor: ProvedorLLM
    registro: Mapping[str, ToolRegistrada]
    parametros: ParametrosDoGrafo
    escrever: Callable[[PropostaDeRegistro], SaidaRegistrarCrm] | None = None
    sessao: Sessao = field(default_factory=sessao_nula)


Atualizacao = dict[str, Any]
No = Callable[[EstadoDoAgente], Atualizacao]


# --- entrada -----------------------------------------------------------------


def sanear(estado: EstadoDoAgente) -> Atualizacao:
    """Guardrail de entrada. Roda antes de qualquer chamada paga."""
    saneamento = sanear_pergunta(estado.get("pergunta", ""))
    atualizacao: Atualizacao = {
        "pergunta": saneamento.texto,
        "achados_pii": list(saneamento.achados),
    }
    if saneamento.bloqueado:
        logger.warning("entrada bloqueada", extra={"motivo": saneamento.motivo})
        return atualizacao | {
            "encerramento": "bloqueada_na_entrada",
            "motivo": saneamento.motivo,
            "resposta": SEM_BASE_NORMATIVA,
        }
    return atualizacao


def rumo_apos_sanear(estado: EstadoDoAgente) -> str:
    return "fim" if estado.get("encerramento") else "deliberar"


# --- deliberação -------------------------------------------------------------


def criar_deliberar(deps: Dependencias) -> No:
    """Uma volta do agente: gerar, despachar tools, colher o que voltou."""

    def deliberar(estado: EstadoDoAgente) -> Atualizacao:
        passo = estado.get("passo", 0) + 1
        if passo > deps.parametros.max_passos:
            logger.warning("limite de passos atingido", extra={"passo": passo})
            return {
                "encerramento": "limite_de_passos",
                "motivo": f"max_passos={deps.parametros.max_passos}",
                "resposta": SEM_BASE_NORMATIVA,
            }

        mensagens = transcricao(estado) or [
            Mensagem.sistema(PROMPT_SISTEMA),
            Mensagem.usuario(estado.get("pergunta", "")),
        ]
        # O span envolve o trabalho de verdade, não o resumo dele: a geração que o
        # `ProvedorRastreado` abre aninha aqui dentro, então o trace mostra quanto
        # da volta foi LLM e quanto foi execução de tool.
        with deps.sessao.no("deliberar.ciclo", tipo="chain") as observacao:
            ciclo = executar_com_autocorrecao(
                deps.provedor,
                mensagens,
                registro=deps.registro,
                max_autocorrecao=deps.parametros.max_autocorrecao,
                temperatura=deps.parametros.temperatura,
                sessao=deps.sessao,
            )
            observacao.atualizar(output=_resumo_do_ciclo(ciclo))

        atualizacao: Atualizacao = {"passo": passo, "mensagens": gravar(list(ciclo.mensagens))}
        if ciclo.abortado:
            return atualizacao | {
                "encerramento": "falha_de_tool",
                "motivo": ciclo.motivo,
                "resposta": SEM_BASE_NORMATIVA,
            }

        atualizacao["trechos"] = _acumular_trechos(estado, ciclo.resultados)
        proposta = _proposta_de(ciclo.resultados)
        if proposta is not None:
            atualizacao["proposta"] = proposta

        resposta = ciclo.resposta
        if resposta is not None and not resposta.pediu_tool:
            atualizacao["resposta"] = resposta.conteudo
            atualizacao["mensagens"] = gravar(
                [*ciclo.mensagens, Mensagem.assistente(resposta.conteudo)]
            )
        return atualizacao

    return deliberar


def _resumo_do_ciclo(ciclo) -> dict[str, Any]:
    """O resumo da volta: o que foi chamado e o que reprovou.

    Aqui só cabe contagem e nome. O conteúdo dos trechos aparece um nível abaixo,
    na observação de cada tool e na entrada da geração — que é onde ele significa
    alguma coisa, porque é lá que se lê *o que o modelo tinha em mãos* ao decidir.
    Repetir isso no resumo do nó só inflaria o payload.

    O que este resumo alimenta é a taxa de erro de tool medida em
    `evals/rodar.py`: `SessaoContadora` lê exatamente estas chaves.
    """
    return {
        "tools_chamadas": [r.tool for r in ciclo.resultados],
        "erros_de_tool": [
            {"tool": r.tool, "tipo": r.erro.tipo} for r in ciclo.resultados if r.erro is not None
        ],
        "tentativas_de_autocorrecao": ciclo.tentativas,
        "abortado": ciclo.abortado,
    }


def _acumular_trechos(estado: EstadoDoAgente, resultados) -> list[dict[str, Any]]:
    """Os trechos desta execução, sem repetir.

    Acumular é o que dá sentido ao validador: a resposta pode citar trecho de um
    passo anterior, e a conferência é contra tudo que foi recuperado na execução,
    não só na última busca.
    """
    trechos = list(estado.get("trechos", []))
    vistos = {t["id"] for t in trechos}
    for resultado in resultados:
        if not isinstance(resultado.saida, SaidaBuscarNormativo):
            continue
        for trecho in resultado.saida.trechos:
            if trecho.id not in vistos:
                vistos.add(trecho.id)
                trechos.append(trecho.model_dump(mode="json"))
    return trechos


def _proposta_de(resultados) -> dict[str, Any] | None:
    """A última proposta de escrita da volta, se houve alguma."""
    for resultado in reversed(list(resultados)):
        if isinstance(resultado.saida, PropostaDeRegistro):
            return resultado.saida.model_dump(mode="json")
    return None


def rumo_apos_deliberar(estado: EstadoDoAgente) -> str:
    if estado.get("encerramento"):
        return "fim"
    if estado.get("proposta"):
        return "aprovar"
    if estado.get("resposta"):
        return "verificar"
    return "deliberar"


# --- verificação de saída ----------------------------------------------------


def criar_verificar(deps: Dependencias) -> No:
    """Guardrail de saída: citação órfã reprova e força reescrita."""

    def verificar(estado: EstadoDoAgente) -> Atualizacao:
        trechos = [TrechoCitado(**t) for t in estado.get("trechos", [])]
        veredito = validar_resposta(estado.get("resposta", ""), trechos)
        if veredito.aprovada:
            return {"encerramento": "respondida", "citacoes": list(veredito.citadas)}

        tentativa = estado.get("tentativa_resposta", 0) + 1
        logger.warning(
            "resposta reprovada no guardrail de saída",
            extra={"motivo": veredito.motivo, "tentativa": tentativa},
        )
        if tentativa >= deps.parametros.max_tentativas_resposta:
            # Dizer "não sei" é resposta. Insistir sem base é o que o projeto não faz.
            return {
                "tentativa_resposta": tentativa,
                "resposta": SEM_BASE_NORMATIVA,
                "encerramento": "sem_base_normativa",
                "motivo": veredito.motivo,
            }

        mensagens = [*transcricao(estado), Mensagem.usuario(veredito.instrucao_de_correcao)]
        return {
            "tentativa_resposta": tentativa,
            "resposta": "",
            "motivo": veredito.motivo,
            "mensagens": gravar(mensagens),
        }

    return verificar


def rumo_apos_verificar(estado: EstadoDoAgente) -> str:
    return "fim" if estado.get("encerramento") else "deliberar"


# --- HITL --------------------------------------------------------------------


def aprovar(estado: EstadoDoAgente) -> Atualizacao:
    """Para aqui e espera o humano. É `interrupt()`, não flag nem espera ativa.

    Na retomada o LangGraph **re-executa este nó desde o início** e `interrupt()`
    devolve o valor enviado. Por isso não pode existir efeito colateral antes da
    chamada: o que estiver acima dela acontece duas vezes.
    """
    decisao = interrupt(
        {
            "tipo": "aprovacao_de_escrita",
            "thread_id": estado.get("thread_id", ""),
            "pergunta": estado.get("pergunta", ""),
            "proposta": estado.get("proposta"),
        }
    )
    return {"aprovacao": _decisao(decisao)}


def _decisao(bruta: Any) -> dict[str, Any]:
    """Normaliza o que o humano mandou. `True` e `{'aprovado': True}` valem o mesmo."""
    if isinstance(bruta, bool):
        return {"aprovado": bruta, "revisor": "", "observacao": ""}
    if isinstance(bruta, Mapping):
        return {
            "aprovado": bool(bruta.get("aprovado", False)),
            "revisor": str(bruta.get("revisor", "")),
            "observacao": str(bruta.get("observacao", "")),
        }
    # Qualquer outra coisa é recusa: aprovação exige forma reconhecível.
    return {"aprovado": False, "revisor": "", "observacao": f"decisão ilegível: {bruta!r}"}


def rumo_apos_aprovar(estado: EstadoDoAgente) -> str:
    aprovacao = estado.get("aprovacao") or {}
    return "escrever" if aprovacao.get("aprovado") else "recusar"


def criar_escrever(deps: Dependencias) -> No:
    """O único ponto de escrita externa do sistema."""

    def escrever(estado: EstadoDoAgente) -> Atualizacao:
        proposta = PropostaDeRegistro(**(estado.get("proposta") or {}))
        if deps.escrever is None:
            registro = {
                "registrado": False,
                "erro": "escrita_nao_configurada",
                "idempotency_key": proposta.idempotency_key,
            }
            nota = "A escrita no CRM não está configurada neste ambiente; nada foi gravado."
        else:
            try:
                saida = deps.escrever(proposta)
            except ErroDeCrm as erro:
                # Falha de escrita não vira sucesso silencioso: fica no estado e
                # na transcrição, e o modelo responde sabendo que não gravou.
                logger.error("escrita no crm falhou", extra={"erro": str(erro)})
                registro = {
                    "registrado": False,
                    "erro": str(erro),
                    "idempotency_key": proposta.idempotency_key,
                }
                nota = f"A escrita no CRM falhou: {erro}. Nada foi gravado."
            else:
                registro = saida.model_dump(mode="json")
                nota = (
                    f"O humano aprovou e o registro {saida.id_registro} "
                    "foi gravado no CRM. Informe isso ao usuário."
                )
        mensagens = [*transcricao(estado), Mensagem.usuario(nota)]
        return {"registro": registro, "proposta": None, "mensagens": gravar(mensagens)}

    return escrever


def recusar(estado: EstadoDoAgente) -> Atualizacao:
    """O humano disse não. A proposta morre aqui e a conversa continua."""
    nota = (
        "O humano recusou o registro no CRM; nada foi gravado. "
        "Responda ao usuário sem registrar a consulta."
    )
    return {
        "registro": {"registrado": False, "erro": "recusado_pelo_humano"},
        "proposta": None,
        "mensagens": gravar([*transcricao(estado), Mensagem.usuario(nota)]),
    }
