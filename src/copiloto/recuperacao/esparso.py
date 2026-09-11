"""Busca léxica (BM25) sobre o índice esparso gravado pela Fase 2.

A razão de existir, em uma frase: **embedding não distingue `4.658` de `4.568`.**
Num corpus em que a pergunta frequentemente é "o que diz a Resolução nº 4.893",
o termo exato é a informação mais valiosa que existe, e é a única que a
similaridade semântica joga fora.

Devolve `(id, score)` e nada mais. O texto do trecho vive no armazenamento
vetorial e é hidratado pelo `retriever` através do adapter — assim este módulo
não conhece Chroma nem duplica documento em disco.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Container
from pathlib import Path
from typing import Any

from copiloto.ingestao.chunking import id_norma_do_chunk
from copiloto.ingestao.indexacao import tokenizar

logger = logging.getLogger(__name__)


class BuscaEsparsa:
    """BM25 reconstruído a partir do corpus tokenizado em `indices/bm25.json`."""

    def __init__(self, ids: list[str], tokens: list[list[str]]) -> None:
        if len(ids) != len(tokens):
            raise ValueError("indice esparso inconsistente: ids e tokens de tamanhos diferentes")
        self._ids = ids
        self._indice: Any | None = None
        self._tokens = tokens
        self._por_norma: dict[str, frozenset[str]] | None = None

    @classmethod
    def carregar(cls, caminho: Path) -> BuscaEsparsa:
        """Lê o JSON gravado pela indexação.

        JSON e não `pickle` — desserializar `pickle` executa código, e um índice
        é arquivo. O objeto BM25 é reconstruído aqui em vez de vir do disco, o
        que também evita amarrar o arquivo à versão da biblioteca.
        """
        payload = json.loads(caminho.read_text(encoding="utf-8"))
        return cls(list(payload["ids"]), [list(t) for t in payload["tokens"]])

    def _carregar(self) -> Any:
        if self._indice is None:
            from rank_bm25 import BM25Okapi

            # BM25Okapi divide pelo comprimento médio dos documentos: corpus
            # vazio viraria divisão por zero lá dentro, então barra aqui.
            if not self._tokens:
                raise ValueError("indice esparso vazio — rodar a indexacao da Fase 2 antes")
            self._indice = BM25Okapi(self._tokens)
        return self._indice

    def __len__(self) -> int:
        return len(self._ids)

    def ids_da_norma(self, id_norma: str) -> frozenset[str]:
        """Os ids do índice que pertencem a uma norma, para `ids_permitidos`.

        Fica aqui, e não em quem chama, porque `self._ids` é o único lugar do
        processo que sabe o que o índice esparso contém — e o mapa é montado uma
        vez por norma, não a cada busca.
        """
        if self._por_norma is None:
            agrupado: dict[str, set[str]] = {}
            for id_ in self._ids:
                agrupado.setdefault(id_norma_do_chunk(id_), set()).add(id_)
            self._por_norma = {norma: frozenset(ids) for norma, ids in agrupado.items()}
        return self._por_norma.get(id_norma, frozenset())

    def buscar(
        self,
        pergunta: str,
        *,
        k: int,
        ids_permitidos: Container[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Os `k` melhores `(id, score)`, do maior score ao menor.

        A pergunta passa pela mesma tokenização do índice — se as duas pontas
        divergirem, o BM25 não acha nada e o erro é silencioso.

        `ids_permitidos` restringe o universo **antes** do corte em `k`, e essa
        ordem é o ponto. Filtrar depois devolveria só os documentos da norma que
        por acaso entrassem no top-`k` global — numa pergunta cujo vocabulário é
        comum a meio corpus, nenhum. Como `get_scores` já pontua todos os
        documentos de qualquer jeito, restringir antes não custa nada a mais.
        """
        consulta = tokenizar(pergunta)
        if not consulta:
            return []
        scores = self._carregar().get_scores(consulta)
        pares = zip(self._ids, scores, strict=True)
        if ids_permitidos is not None:
            pares = ((id_, s) for id_, s in pares if id_ in ids_permitidos)
        ordenados = sorted(pares, key=lambda par: -par[1])
        # Score zero significa nenhum termo em comum: é ruído entrando na fusão
        # com posto alto, e posto é tudo que o RRF olha.
        return [(id_, float(score)) for id_, score in ordenados[:k] if score > 0.0]
