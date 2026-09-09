"""Busca vetorial: pergunta -> embedding -> vizinhos no adapter.

O que o denso resolve e o BM25 não: "requisitos para contratação de nuvem" acha
o artigo que fala em "serviços de processamento e armazenamento de dados" sem
compartilhar uma palavra com ele. O que ele erra e o BM25 acerta está em
`esparso.py`.

O modelo ONNX é carregado na primeira busca e reutilizado — instanciar
`TextEmbedding` dentro de laço estoura os 8 GB da máquina (§3.1 do briefing).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from copiloto.recuperacao.adapters.base import AdaptadorVetorial, Ocorrencia

logger = logging.getLogger(__name__)


class BuscaDensa:
    """Vetoriza a pergunta e consulta o adapter. Não conhece o Chroma."""

    def __init__(self, adaptador: AdaptadorVetorial, *, modelo: str) -> None:
        self._adaptador = adaptador
        self._modelo = modelo
        self._embedder: Any | None = None

    def _carregar(self) -> Any:
        """Carrega sob demanda, uma vez. Nunca dentro de laço."""
        if self._embedder is None:
            from fastembed import TextEmbedding

            logger.info("carregando modelo de embedding", extra={"modelo": self._modelo})
            self._embedder = TextEmbedding(model_name=self._modelo)
        return self._embedder

    def vetorizar(self, texto: str) -> list[float]:
        """Um texto, um vetor. `fastembed` devolve gerador de `ndarray`."""
        (vetor,) = self._carregar().embed([texto])
        return [float(x) for x in vetor]

    def buscar(
        self,
        pergunta: str,
        *,
        k: int,
        filtro: Mapping[str, Any] | None = None,
    ) -> list[Ocorrencia]:
        return self._adaptador.buscar(self.vetorizar(pergunta), k=k, filtro=filtro)

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        """Hidrata ids vindos do BM25. Delegação pura — quem guarda texto é o adapter."""
        return self._adaptador.obter(ids)
