"""Adapter do Chroma local — o vetorial do desenvolvimento e do deploy gratuito.

Único módulo do projeto autorizado a importar `chromadb`, e o import é feito
dentro da função para que abrir este arquivo não custe carregar a biblioteca.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from copiloto.recuperacao.adapters.base import Ocorrencia

logger = logging.getLogger(__name__)


class AdaptadorChroma:
    """Implementa `AdaptadorVetorial` sobre uma coleção Chroma persistente."""

    def __init__(self, colecao: Any) -> None:
        self._colecao = colecao

    @classmethod
    def abrir(cls, caminho: Path, *, colecao: str) -> AdaptadorChroma:
        """Abre a coleção criada pela indexação da Fase 2."""
        import chromadb

        cliente = chromadb.PersistentClient(path=str(caminho))
        return cls(
            cliente.get_or_create_collection(name=colecao, metadata={"hnsw:space": "cosine"})
        )

    def buscar(
        self,
        vetor: Sequence[float],
        *,
        k: int,
        filtro: Mapping[str, Any] | None = None,
    ) -> list[Ocorrencia]:
        bruto = self._colecao.query(
            query_embeddings=[list(vetor)],
            n_results=k,
            where=dict(filtro) if filtro else None,
            include=["documents", "metadatas", "distances"],
        )
        return [
            # A coleção foi criada com `hnsw:space = cosine`, então a distância
            # vive em [0, 2] e `1 - d` devolve a similaridade. A conversão é
            # cosmética: a fusão usa posto, não valor.
            Ocorrencia(id=id_, texto=doc or "", metadata=meta or {}, score=1.0 - float(dist))
            for id_, doc, meta, dist in zip(
                _primeira(bruto, "ids"),
                _primeira(bruto, "documents"),
                _primeira(bruto, "metadatas"),
                _primeira(bruto, "distances"),
                strict=False,
            )
        ]

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        pedidos = list(dict.fromkeys(ids))
        if not pedidos:
            return {}
        bruto = self._colecao.get(ids=pedidos, include=["documents", "metadatas"])
        return {
            id_: Ocorrencia(id=id_, texto=doc or "", metadata=meta or {}, score=0.0)
            for id_, doc, meta in zip(
                bruto.get("ids") or [],
                bruto.get("documents") or [],
                bruto.get("metadatas") or [],
                strict=False,
            )
        }


def _primeira(bruto: Mapping[str, Any], chave: str) -> list[Any]:
    """`query` do Chroma devolve uma lista por vetor consultado; consultamos um."""
    valores = bruto.get(chave) or []
    return list(valores[0]) if valores else []
