"""Re-ranking com FlashRank (cross-encoder ONNX, CPU).

Denso e esparso pontuam pergunta e trecho separadamente; o cross-encoder lê os
dois juntos e responde "este trecho responde esta pergunta?". É caro, então só
os candidatos que sobreviveram à fusão chegam aqui.

Se ele não melhorar o número, isso vai para o README como resultado negativo
(§6 do briefing). A tabela de ablação é que decide, não a expectativa.

**Como escolher o modelo, e por que não é o multilíngue.** A escolha óbvia para
corpus em português era `ms-marco-MultiBERT-L-12`. Medido, ele satura: em 50
candidatos os scores ficam todos entre 0,993 e 0,999, um espalhamento de 0,006.
Score saturado não ordena nada — a saída vira ruído, e o efeito é ativo, não
neutro: o artigo correto caiu do 6º para o 43º lugar. O `ms-marco-MiniLM-L-12-v2`,
treinado em inglês, espalha 0,49 no mesmo conjunto e ordena. O diagnóstico útil
não é o recall: é o **espalhamento dos scores**. Antes de trocar o modelo daqui,
medir isso — modelo que satura reprova sem precisar rodar a ablação inteira.

Um único modelo ONNX por vez: o `Ranker` é carregado na primeira chamada e
reutilizado, e o `fastembed` do denso já terminou seu trabalho quando o rerank
começa.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from copiloto.recuperacao.adapters.base import Ocorrencia

logger = logging.getLogger(__name__)


class Reordenador:
    """Reordena ocorrências por relevância à pergunta."""

    def __init__(self, *, modelo: str, cache: str | None = None) -> None:
        self._modelo = modelo
        self._cache = cache
        self._ranker: Any | None = None

    def _carregar(self) -> Any:
        if self._ranker is None:
            from flashrank import Ranker

            logger.info("carregando modelo de rerank", extra={"modelo": self._modelo})
            # `cache_dir` explícito: o default do FlashRank é `/tmp`, que no
            # Windows vira uma pasta na raiz do disco e rebaixa o modelo a cada
            # execução. O peso ONNX mora junto dos índices, que já são ignorados
            # pelo git.
            self._ranker = Ranker(
                model_name=self._modelo,
                cache_dir=self._cache or str(Path(tempfile.gettempdir()) / "flashrank"),
            )
        return self._ranker

    def reordenar(self, pergunta: str, ocorrencias: Sequence[Ocorrencia]) -> list[Ocorrencia]:
        """Devolve as mesmas ocorrências, reordenadas e com `score` do cross-encoder.

        O score substitui o da origem porque, a partir daqui, é ele que o
        `score_minimo` compara. Não filtra nada — o corte é decisão do
        `retriever`, que é quem lê `parametros.toml`.
        """
        if not ocorrencias:
            return []
        from flashrank import RerankRequest

        passagens = [{"id": o.id, "text": o.texto} for o in ocorrencias]
        por_id = {o.id: o for o in ocorrencias}
        resultado = self._carregar().rerank(RerankRequest(query=pergunta, passages=passagens))
        return [
            Ocorrencia(
                id=por_id[item["id"]].id,
                texto=por_id[item["id"]].texto,
                metadata=por_id[item["id"]].metadata,
                score=float(item["score"]),
            )
            for item in resultado
            if item["id"] in por_id
        ]
