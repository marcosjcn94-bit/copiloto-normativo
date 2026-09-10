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
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from copiloto.grafo.estado import EstadoDoAgente
from copiloto.grafo.nos import (
    Dependencias,
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

__all__ = [
    "Dependencias",
    "ParametrosDoGrafo",
    "carregar_parametros",
    "checkpointer_sqlite",
    "compilar",
    "montar_grafo",
]


def montar_grafo(deps: Dependencias) -> StateGraph:
    """O grafo sem checkpointer — útil para desenhar e inspecionar."""
    grafo: StateGraph = StateGraph(EstadoDoAgente)

    grafo.add_node("sanear", sanear)
    grafo.add_node("deliberar", criar_deliberar(deps))
    grafo.add_node("verificar", criar_verificar(deps))
    grafo.add_node("aprovar", aprovar)
    grafo.add_node("escrever", criar_escrever(deps))
    grafo.add_node("recusar", recusar)

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
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(caminho), check_same_thread=False)
    checkpointer = SqliteSaver(conexao)
    checkpointer.setup()
    return checkpointer, conexao
