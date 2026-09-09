"""BM25: o que existe para achar `4.658` quando o embedding não distingue de `4.568`."""

from __future__ import annotations

import json

import pytest

from copiloto.recuperacao.esparso import BuscaEsparsa

pytest.importorskip("rank_bm25")

CORPUS = {
    "c1": "Art. 12 As instituicoes previamente a contratacao de servicos de computacao em nuvem",
    "c2": "Art. 27 Ficam revogadas a Resolucao no 4.658 de 26 de abril de 2018",
    "c3": "Art. 3o A politica de seguranca cibernetica deve contemplar no minimo os objetivos",
}


def _indice() -> BuscaEsparsa:
    from copiloto.ingestao.indexacao import tokenizar

    ids = list(CORPUS)
    return BuscaEsparsa(ids, [tokenizar(CORPUS[id_]) for id_ in ids])


def test_acha_pelo_numero_da_norma() -> None:
    """A razão de existir do índice esparso, em um assert."""
    (melhor, _), *_ = _indice().buscar("o que diz a Resolução nº 4.658", k=3)
    assert melhor == "c2"


def test_pontuacao_no_numero_nao_atrapalha() -> None:
    """`4.658` e `4658` têm de cair no mesmo token dos dois lados da busca."""
    assert _indice().buscar("resolucao 4658", k=3)[0][0] == "c2"


def test_termo_sem_correspondencia_nao_devolve_ruido() -> None:
    """Score zero entrando na fusão ocuparia posto alto sem ter achado nada."""
    assert _indice().buscar("criptomoeda estrangeira", k=3) == []


def test_pergunta_so_de_pontuacao_devolve_vazio() -> None:
    assert _indice().buscar("???", k=3) == []


def test_indice_vazio_falha_com_mensagem_util() -> None:
    with pytest.raises(ValueError, match="vazio"):
        BuscaEsparsa([], []).buscar("qualquer", k=3)


def test_ids_e_tokens_de_tamanhos_diferentes_falham_no_construtor() -> None:
    with pytest.raises(ValueError, match="inconsistente"):
        BuscaEsparsa(["a", "b"], [["x"]])


def test_carrega_do_json_gravado_pela_indexacao(tmp_path) -> None:
    """O formato em disco é contrato entre a Fase 2 e a Fase 3.

    Três documentos e não um: com corpus de um só, todo termo tem IDF negativo
    no BM25Okapi e nada pontua acima de zero. É artefato de corpus de brinquedo,
    não do índice — o real tem milhares de chunks.
    """
    caminho = tmp_path / "bm25.json"
    caminho.write_text(
        json.dumps(
            {
                "ids": ["c1", "c2", "c3"],
                "tokens": [["nuvem", "contratacao"], ["incidente", "resposta"], ["diretor"]],
            }
        ),
        encoding="utf-8",
    )
    indice = BuscaEsparsa.carregar(caminho)
    assert len(indice) == 3
    assert indice.buscar("contratação de nuvem", k=1)[0][0] == "c1"
