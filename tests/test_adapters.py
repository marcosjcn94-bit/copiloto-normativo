"""O `Protocol` do vetorial só vale se as duas implementações realmente o satisfazem.

O adapter do Azure não tem serviço para conversar (sem free tier, §3 do
briefing), então o que se verifica dele é o contrato: que satisfaz o `Protocol`
e que monta o corpo REST certo contra um cliente falso. É menos que um teste de
integração e está declarado como tal — inclusive no cabeçalho do módulo.
"""

from __future__ import annotations

from typing import Any

import pytest

from copiloto.recuperacao.adapters.azure_ai_search import (
    AdaptadorAzureAISearch,
    _odata,
)
from copiloto.recuperacao.adapters.base import AdaptadorVetorial


class RespostaFalsa:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class ClienteFalso:
    """Registra o que foi enviado; não fala com rede nenhuma."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def post(self, caminho: str, *, params: dict[str, Any], json: dict[str, Any]) -> RespostaFalsa:
        self.chamadas.append((caminho, json))
        return RespostaFalsa(self._payload)


def test_adapter_do_azure_satisfaz_o_protocolo() -> None:
    assert isinstance(AdaptadorAzureAISearch(ClienteFalso({}), indice="i"), AdaptadorVetorial)


def test_adapter_do_chroma_satisfaz_o_protocolo() -> None:
    pytest.importorskip("chromadb")
    from copiloto.recuperacao.adapters.chroma import AdaptadorChroma

    assert isinstance(AdaptadorChroma(colecao=object()), AdaptadorVetorial)


def test_busca_no_azure_manda_o_vetor_e_o_k() -> None:
    cliente = ClienteFalso(
        {"value": [{"id": "c1", "texto": "trecho", "artigo": "Art. 7º", "@search.score": 0.9}]}
    )
    adaptador = AdaptadorAzureAISearch(cliente, indice="normativos")

    (ocorrencia,) = adaptador.buscar([0.1, 0.2], k=3)

    (caminho, corpo) = cliente.chamadas[0]
    assert caminho == "/indexes/normativos/docs/search"
    assert corpo["vectorQueries"][0]["vector"] == [0.1, 0.2]
    assert corpo["vectorQueries"][0]["k"] == 3
    assert ocorrencia.id == "c1"
    assert ocorrencia.metadata["artigo"] == "Art. 7º"
    assert "@search.score" not in ocorrencia.metadata, "campo de serviço vazou para a metadata"


def test_obter_sem_ids_nao_chama_o_servico() -> None:
    """Um `IN ()` vazio viraria uma varredura no índice inteiro."""
    cliente = ClienteFalso({"value": []})
    assert AdaptadorAzureAISearch(cliente, indice="i").obter([]) == {}
    assert cliente.chamadas == []


def test_odata_escapa_apostrofo() -> None:
    """`Resolução 'X'` num filtro quebraria a expressão — e é entrada de usuário."""
    assert _odata({"norma": "Resolu'cao"}) == "norma eq 'Resolu''cao'"


def test_odata_traduz_lista_para_alternativas() -> None:
    assert _odata({"id": ["a", "b"]}) == "(id eq 'a' or id eq 'b')"


def test_odata_nao_poe_aspas_em_booleano_nem_numero() -> None:
    assert _odata({"revogada": False, "ano": 2021}) == "revogada eq false and ano eq 2021"
