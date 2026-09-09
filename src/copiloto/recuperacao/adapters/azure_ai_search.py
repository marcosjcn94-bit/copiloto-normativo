"""Adapter do Azure AI Search — a prova de que o `Protocol` paga o próprio custo.

**Não exercitado contra um serviço real.** O Azure AI Search não tem free tier
permanente e o projeto é inegociavelmente gratuito (§3 do briefing), então este
adapter é verificado apenas por teste de contrato: que ele satisfaz
`AdaptadorVetorial` e que monta o corpo REST esperado. Dizer isso aqui é mais
honesto que deixar o leitor supor que rodou em produção.

O que ele demonstra é arquitetural e vale independente disso: trocar Chroma por
Azure é construir outro objeto, não mexer em `retriever.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from copiloto.recuperacao.adapters.base import Ocorrencia

logger = logging.getLogger(__name__)

VERSAO_API = "2024-07-01"

# Nomes dos campos no índice do Azure. O índice espelha o do Chroma: id do chunk
# filho, texto indexável, vetor e a metadata que vira citação.
CAMPO_ID = "id"
CAMPO_TEXTO = "texto"
CAMPO_VETOR = "vetor"


class AdaptadorAzureAISearch:
    """Implementa `AdaptadorVetorial` sobre a REST API do Azure AI Search."""

    def __init__(self, cliente: Any, *, indice: str) -> None:
        """`cliente` é um `httpx.Client` já com `base_url` e `api-key` no header.

        Receber o cliente pronto em vez de montá-lo aqui mantém o adapter
        testável sem rede e sem segredo em código.
        """
        self._cliente = cliente
        self._indice = indice

    def buscar(
        self,
        vetor: Sequence[float],
        *,
        k: int,
        filtro: Mapping[str, Any] | None = None,
    ) -> list[Ocorrencia]:
        corpo: dict[str, Any] = {
            "count": False,
            "select": f"{CAMPO_ID},{CAMPO_TEXTO}",
            "vectorQueries": [
                {"kind": "vector", "vector": list(vetor), "fields": CAMPO_VETOR, "k": k}
            ],
        }
        if filtro:
            corpo["filter"] = _odata(filtro)
        resposta = self._cliente.post(
            f"/indexes/{self._indice}/docs/search",
            params={"api-version": VERSAO_API},
            json=corpo,
        )
        resposta.raise_for_status()
        return [_para_ocorrencia(doc) for doc in resposta.json().get("value", [])]

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        pedidos = list(dict.fromkeys(ids))
        if not pedidos:
            return {}
        resposta = self._cliente.post(
            f"/indexes/{self._indice}/docs/search",
            params={"api-version": VERSAO_API},
            json={
                "search": "*",
                "top": len(pedidos),
                "filter": _odata({CAMPO_ID: pedidos}),
                "select": f"{CAMPO_ID},{CAMPO_TEXTO}",
            },
        )
        resposta.raise_for_status()
        achados = [_para_ocorrencia(doc) for doc in resposta.json().get("value", [])]
        return {ocorrencia.id: ocorrencia for ocorrencia in achados}


def _para_ocorrencia(documento: Mapping[str, Any]) -> Ocorrencia:
    """`@search.score` é o score do Azure; a metadata é o que sobra do documento."""
    metadata = {
        chave: valor
        for chave, valor in documento.items()
        if chave not in {CAMPO_ID, CAMPO_TEXTO, CAMPO_VETOR} and not chave.startswith("@")
    }
    return Ocorrencia(
        id=str(documento.get(CAMPO_ID, "")),
        texto=str(documento.get(CAMPO_TEXTO, "")),
        metadata=metadata,
        score=float(documento.get("@search.score", 0.0)),
    )


def _odata(filtro: Mapping[str, Any]) -> str:
    """Traduz o filtro do projeto para OData.

    Só o que a recuperação usa: igualdade e pertinência a uma lista. Aspas
    simples são escapadas dobrando, que é a regra do OData — sem isso um valor
    com apóstrofo quebraria a expressão.
    """
    clausulas: list[str] = []
    for campo, valor in filtro.items():
        if isinstance(valor, (list, tuple, set)):
            alternativas = " or ".join(f"{campo} eq {_literal(v)}" for v in valor)
            clausulas.append(f"({alternativas})")
        else:
            clausulas.append(f"{campo} eq {_literal(valor)}")
    return " and ".join(clausulas)


def _literal(valor: Any) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return str(valor)
    return "'" + str(valor).replace("'", "''") + "'"
