"""Contrato do armazenamento vetorial.

O resto do projeto nunca importa `chromadb`. Quem precisa de vetorial pede este
`Protocol` e recebe um adapter — é esse desacoplamento que faz a troca por Azure
AI Search ser uma linha de configuração e não uma refatoração.

Duas operações, e só: `buscar` por similaridade e `obter` por id. `obter` existe
porque o BM25 devolve id sem texto — o índice esparso guarda tokens, não
documento —, e alguém precisa hidratar esse id antes do rerank. A hidratação
mora aqui e não no `esparso.py` justamente para o esparso não conhecer o
vetorial.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Ocorrencia:
    """Um chunk filho recuperado, com o score cru de quem o achou.

    `score` é comparável apenas dentro da mesma origem: similaridade de cosseno
    do denso, peso BM25 do esparso e probabilidade do cross-encoder no rerank
    vivem em escalas diferentes. É exatamente por isso que a fusão é por posto
    (RRF) e não por soma de score.
    """

    id: str
    texto: str
    metadata: Mapping[str, Any]
    score: float

    @property
    def id_pai(self) -> str:
        """Id do artigo a que o trecho pertence — a unidade que se entrega."""
        return str(self.metadata.get("id_pai", ""))


@runtime_checkable
class AdaptadorVetorial(Protocol):
    """O mínimo que a recuperação exige de um armazenamento vetorial."""

    def buscar(
        self,
        vetor: Sequence[float],
        *,
        k: int,
        filtro: Mapping[str, Any] | None = None,
    ) -> list[Ocorrencia]:
        """Os `k` vizinhos mais próximos de `vetor`, do mais próximo ao menos."""
        ...

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        """Hidrata ids em ocorrências. Id inexistente sai do resultado, não quebra."""
        ...
