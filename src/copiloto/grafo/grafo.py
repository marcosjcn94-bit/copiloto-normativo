"""O `StateGraph`, o checkpointer e a fiação das arestas.

O desenho cabe em uma frase: **sanear, deliberar, verificar — e, se houver
escrita, parar e esperar um humano**.

```
        START
          │
        sanear ──(bloqueada)──────────────────────────► END
          │
          ▼
   ┌── deliberar ──(sem base / limite de passos)──────► END
   │      │  ▲
   │      │  └──────────────┬─────────────┐
   │   (propôs escrita)     │             │
   │      ▼                 │             │
   │   aprovar ── interrupt()             │
   │    │     └──(recusado)──► recusar ───┘
   │    └──(aprovado)────────► escrever ──┘
   │
   └──(respondeu)──► verificar ──(aprovada)──────────► END
                         └──(reprovada, tem tentativa)──► deliberar
```

**Por que o ciclo passa sempre pela aresta.** Nenhum nó chama outro nó: cada um
grava no estado e a condição decide. É o que permite trocar o critério de parada
sem reescrever nó nenhum — e é o que torna o grafo desenhável, que é metade do
valor de usar grafo em vez de um `while`.

**O checkpointer não é detalhe de infraestrutura.** É ele que faz o
`interrupt()` valer: o estado da conversa fica no SQLite, e retomar depois de o
processo morrer continua de onde parou, sem repetir as chamadas de LLM já pagas.

**O trace envolve cada nó, não o grafo inteiro (Fase 7).** `montar_grafo` embrulha
cada função de nó num span antes de registrá-la. É aqui e não dentro dos nós
porque a instrumentação é uma preocupação do desenho do grafo: um nó novo nasce
observado sem que ninguém se lembre de instrumentá-lo, e nenhum nó ganha uma
linha de `try/finally` que não tem a ver com o que ele decide.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from copiloto.grafo.estado import EstadoDoAgente
from copiloto.grafo.nos import (
    Atualizacao,
    Dependencias,
    No,
    ParametrosDoGrafo,
    aprovar,
    carregar_parametros,
    criar_deliberar,
    criar_escrever,
    criar_verificar,
    recusar,
    rumo_apos_aprovar,
    rumo_apos_deliberar,
    rumo_apos_sanear,
    rumo_apos_verificar,
    sanear,
)
from copiloto.observabilidade.tracing import TipoDeObservacao

__all__ = [
    "Dependencias",
    "ParametrosDoGrafo",
    "carregar_parametros",
    "checkpointer_sqlite",
    "compilar",
    "montar_grafo",
]


# O que de cada atualização de estado vale a pena aparecer no trace. A lista é
# explícita e curta de propósito: nenhuma chave que carregue texto de norma,
# transcrição ou pergunta entra aqui. Quem quiser ler o conteúdo lê o checkpoint,
# que fica na máquina; o trace vai para um terceiro.
CHAVES_OBSERVAVEIS = (
    "passo",
    "encerramento",
    "motivo",
    "tentativa_resposta",
    "achados_pii",
)


def _observavel(atualizacao: Mapping[str, Any]) -> dict[str, Any]:
    """O resumo do que o nó decidiu, sem o texto do que ele leu."""
    resumo = {k: atualizacao[k] for k in CHAVES_OBSERVAVEIS if k in atualizacao}
    if "trechos" in atualizacao:
        resumo["trechos_acumulados"] = len(atualizacao["trechos"])
    if "citacoes" in atualizacao:
        resumo["citacoes"] = list(atualizacao["citacoes"])
    if "proposta" in atualizacao:
        resumo["propos_escrita"] = atualizacao["proposta"] is not None
    if "registro" in atualizacao:
        registro = atualizacao["registro"] or {}
        resumo["registrado"] = bool(registro.get("registrado"))
    return resumo


# O tipo de observação de cada nó, e cada escolha diz o que o nó é. `sanear` e
# `verificar` são `guardrail` porque é isso que eles são — e porque o Langfuse
# deixa filtrar guardrails para medir quanto o de saída reprova. `deliberar` é
# `agent`: é o laço que decide e chama tool, e é o nó que vira nó do agent graph.
# `escrever` é `tool` porque é a única ação com efeito fora do processo.
TIPO_DO_NO: dict[str, TipoDeObservacao] = {
    "sanear": "guardrail",
    "deliberar": "agent",
    "verificar": "guardrail",
    "aprovar": "span",
    "escrever": "tool",
    "recusar": "span",
}


def _observado(no: No, *, nome: str, deps: Dependencias) -> No:
    """O mesmo nó, dentro de um span. Assinatura idêntica — o grafo não percebe.

    `GraphBubbleUp` é o canal de controle do LangGraph, e o `interrupt()` do nó
    `aprovar` viaja por ele. Deixá-lo escapar como exceção qualquer pintaria de
    vermelho, em todo trace com aprovação humana, justamente o nó que funcionou:
    parar e esperar é o comportamento correto, não uma falha.
    """

    def envolvido(estado: EstadoDoAgente) -> Atualizacao:
        with deps.sessao.no(nome, tipo=TIPO_DO_NO.get(nome, "span")) as observacao:
            try:
                atualizacao = no(estado)
            except GraphBubbleUp:
                observacao.atualizar(output={"pausado": True, "motivo": "aguardando_humano"})
                raise
            except Exception as erro:
                observacao.atualizar(level="ERROR", status_message=str(erro))
                raise
            observacao.atualizar(output=_observavel(atualizacao))
            return atualizacao

    envolvido.__name__ = nome
    return envolvido


def montar_grafo(deps: Dependencias) -> StateGraph:
    """O grafo sem checkpointer — útil para desenhar e inspecionar."""
    grafo: StateGraph = StateGraph(EstadoDoAgente)

    for nome, no in (
        ("sanear", sanear),
        ("deliberar", criar_deliberar(deps)),
        ("verificar", criar_verificar(deps)),
        ("aprovar", aprovar),
        ("escrever", criar_escrever(deps)),
        ("recusar", recusar),
    ):
        grafo.add_node(nome, _observado(no, nome=nome, deps=deps))

    grafo.add_edge(START, "sanear")
    grafo.add_conditional_edges("sanear", rumo_apos_sanear, {"deliberar": "deliberar", "fim": END})
    grafo.add_conditional_edges(
        "deliberar",
        rumo_apos_deliberar,
        {"deliberar": "deliberar", "verificar": "verificar", "aprovar": "aprovar", "fim": END},
    )
    grafo.add_conditional_edges(
        "verificar", rumo_apos_verificar, {"deliberar": "deliberar", "fim": END}
    )
    grafo.add_conditional_edges(
        "aprovar", rumo_apos_aprovar, {"escrever": "escrever", "recusar": "recusar"}
    )
    # Depois de resolvida a escrita, a conversa volta ao agente: ele ainda tem de
    # responder ao usuário, e agora sabe se gravou ou não.
    grafo.add_edge("escrever", "deliberar")
    grafo.add_edge("recusar", "deliberar")
    return grafo


def compilar(deps: Dependencias, *, checkpointer: SqliteSaver) -> CompiledStateGraph:
    """O grafo pronto para rodar. Sem checkpointer o `interrupt()` não teria onde parar."""
    return montar_grafo(deps).compile(checkpointer=checkpointer)


def checkpointer_sqlite(caminho: Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    """Checkpointer em arquivo, mais a conexão — quem abre é quem fecha.

    `check_same_thread=False` porque a API da Fase 6 atende em thread de worker e
    o mesmo checkpointer serve as duas. A conexão volta junto de propósito: um
    checkpointer que fecha a própria conexão no `__del__` vira bug de ordem de
    coleta, e um que nunca fecha vira arquivo travado no Windows.

    `nolock=1` na URI porque o Azure Files monta como SMB, e o lock de arquivo
    do SQLite (fcntl por baixo) não é confiável nesse protocolo — o primeiro
    `setup()` já falhava com `database is locked` sem nenhum segundo processo
    disputando o arquivo. É seguro pular o lock porque a invariante já existe
    por outro caminho: `maxReplicas: 1` no Bicep garante um único processo
    tocando este arquivo. Em disco local (dev) o parâmetro é inofensivo.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(f"file:{caminho}?nolock=1", uri=True, check_same_thread=False)
    checkpointer = SqliteSaver(conexao)
    checkpointer.setup()
    return checkpointer, conexao
