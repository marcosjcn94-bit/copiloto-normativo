"""Reciprocal Rank Fusion — junta as listas do denso e do esparso.

Por que posto e não score: similaridade de cosseno e peso BM25 não são
comparáveis. Somá-los exigiria normalizar duas distribuições que mudam a cada
pergunta, e qualquer peso escolhido seria arbitrário. O RRF ignora o valor e
olha só a posição:

    score(d) = Σ 1 / (k_rrf + posto(d, lista))

`k_rrf` amortece o topo: com 60, a diferença entre 1º e 2º lugar (1/61 vs 1/62)
é pequena, então um documento precisa aparecer bem em **várias** listas para
subir. É o comportamento que se quer — concordância entre buscas de naturezas
diferentes é sinal mais forte que primeiro lugar em uma só.

Sem estado e sem I/O: é aqui que a fusão fica testável sem carregar modelo.
"""

from __future__ import annotations

from collections.abc import Sequence


def rrf(rankings: Sequence[Sequence[str]], *, k_rrf: int) -> list[tuple[str, float]]:
    """Funde listas ordenadas de ids em uma só, do melhor para o pior.

    `rankings` é uma lista por origem, cada uma já ordenada. Ids repetidos
    dentro da mesma lista contam uma vez (o primeiro posto). Empate é desfeito
    pelo id, para que a saída seja determinística — sem isso a tabela de
    ablação mudaria de valor entre execuções.
    """
    if k_rrf <= 0:
        raise ValueError("k_rrf deve ser positivo")

    acumulado: dict[str, float] = {}
    for lista in rankings:
        for posto, id_ in enumerate(dict.fromkeys(lista), start=1):
            acumulado[id_] = acumulado.get(id_, 0.0) + 1.0 / (k_rrf + posto)
    return sorted(acumulado.items(), key=lambda par: (-par[1], par[0]))
