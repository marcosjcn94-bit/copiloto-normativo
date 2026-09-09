"""Critério de pronto da Fase 2, medido sobre os índices realmente construídos.

Roda contra o que `python -m copiloto.ingestao.indexacao` deixou em disco. Sem
esses artefatos o teste é pulado — ele afere o corpus, não o constrói.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from copiloto.ingestao.coleta import carregar_corpus

pytestmark = pytest.mark.corpus

RAIZ = Path(__file__).resolve().parents[1]
CATALOGO = RAIZ / "data" / "catalogo.sqlite"
BM25 = RAIZ / "indices" / "bm25.json"


@pytest.fixture(scope="module")
def catalogo() -> sqlite3.Connection:
    if not CATALOGO.exists():
        pytest.skip("catálogo ausente: rode python -m copiloto.ingestao.indexacao")
    conexao = sqlite3.connect(CATALOGO)
    conexao.row_factory = sqlite3.Row
    return conexao


@pytest.fixture(scope="module")
def normas_declaradas():
    return carregar_corpus(RAIZ / "config" / "corpus.toml").normas


def test_contagem_do_catalogo_bate_com_o_corpus_toml(catalogo, normas_declaradas) -> None:
    (total,) = catalogo.execute("SELECT count(*) FROM normas").fetchone()
    assert total == len(normas_declaradas)


def test_ao_menos_uma_norma_esta_marcada_como_revogada(catalogo) -> None:
    (revogadas,) = catalogo.execute(
        "SELECT count(*) FROM normas WHERE status_vigencia = 'revogada'"
    ).fetchone()
    assert revogadas >= 1


def test_toda_norma_do_catalogo_rendeu_artigo(catalogo) -> None:
    orfas = [
        linha["id_norma"]
        for linha in catalogo.execute("SELECT id_norma FROM normas WHERE artigos = 0")
    ]
    assert not orfas, f"normas sem artigo extraído: {orfas}"


def test_todo_artigo_pertence_a_uma_norma_do_catalogo(catalogo) -> None:
    (soltos,) = catalogo.execute(
        "SELECT count(*) FROM artigos WHERE id_norma NOT IN (SELECT id_norma FROM normas)"
    ).fetchone()
    assert soltos == 0


def test_indice_esparso_cobre_os_mesmos_chunks_do_denso(catalogo) -> None:
    if not BM25.exists():
        pytest.skip("índice BM25 ausente")
    payload = json.loads(BM25.read_text(encoding="utf-8"))
    assert len(payload["ids"]) == len(set(payload["ids"])), "id de chunk duplicado no BM25"

    chromadb = pytest.importorskip("chromadb")
    from copiloto.recuperacao import carregar_parametros

    parametros = carregar_parametros(RAIZ / "config" / "parametros.toml")
    caminho = RAIZ / "indices" / "chroma"
    if not caminho.exists():
        pytest.skip("índice denso ausente")
    colecao = chromadb.PersistentClient(path=str(caminho)).get_collection(parametros.colecao)
    assert colecao.count() == len(payload["ids"])


def test_vigencia_coletada_e_a_declarada_no_corpus(catalogo, normas_declaradas) -> None:
    """Divergência aqui não é bug: é a fonte tendo revogado algo depois da curadoria."""
    coletado = {
        linha["id_norma"]: linha["status_vigencia"]
        for linha in catalogo.execute("SELECT id_norma, status_vigencia FROM normas")
    }
    divergentes = {
        n.id_norma: (n.status_vigencia, coletado[n.id_norma])
        for n in normas_declaradas
        if coletado.get(n.id_norma) != n.status_vigencia
    }
    assert not divergentes, f"corpus.toml desatualizado em: {divergentes}"
