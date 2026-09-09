"""Indexação: catálogo SQL, tabela de artigos, BM25 e reexecução."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from copiloto.ingestao.coleta import NormaDoCorpus
from copiloto.ingestao.indexacao import abrir_catalogo, indexar, tokenizar

TEXTO = (
    "<p>CAPÍTULO I</p><p>DO OBJETO</p>"
    "<p>Art. 1º Esta Resolução dispõe sobre computação em nuvem.</p>"
    "<p>§ 1º O contrato deve prever auditoria.</p>"
    "<p>Art. 2º A política é anual.</p>"
)


def norma(numero: str = "4893", tema: str = "computacao_em_nuvem") -> NormaDoCorpus:
    return NormaDoCorpus(
        tipo="Resolução CMN",
        numero=numero,
        ano=2021,
        data=date(2021, 2, 26),
        tema=tema,
        status_vigencia="vigente",
        url="https://exemplo/norma",
    )


def gravar_bruto(pasta: Path, n: NormaDoCorpus, *, revogado: bool = False, texto: str = TEXTO):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{n.id_norma}.json").write_text(
        json.dumps(
            {
                "Titulo": f"Resolução Nº {n.numero}",
                "Assunto": "Dispõe sobre nuvem.",
                "Revogado": revogado,
                "Texto": texto,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def catalogo(tmp_path: Path) -> sqlite3.Connection:
    return abrir_catalogo(tmp_path / "catalogo.sqlite")


def test_catalogo_recebe_uma_linha_por_norma(tmp_path: Path, catalogo: sqlite3.Connection) -> None:
    bruto = tmp_path / "bruto"
    normas = [norma("4893"), norma("4658", tema="seguranca_cibernetica")]
    for n in normas:
        gravar_bruto(bruto, n)

    relatorio = indexar(normas, bruto=bruto, conexao=catalogo)

    assert relatorio.normas == 2
    (total,) = catalogo.execute("SELECT count(*) FROM normas").fetchone()
    assert total == len(normas), "o count do catálogo tem que bater com o corpus"


def test_vigencia_vem_da_fonte_e_nao_do_toml(tmp_path: Path, catalogo: sqlite3.Connection) -> None:
    bruto = tmp_path / "bruto"
    n = norma()  # declarada vigente no corpus
    gravar_bruto(bruto, n, revogado=True)

    indexar([n], bruto=bruto, conexao=catalogo)

    linha = catalogo.execute(
        "SELECT status_vigencia FROM normas WHERE id_norma = ?", (n.id_norma,)
    ).fetchone()
    assert linha["status_vigencia"] == "revogada"


def test_reexecutar_nao_duplica_nem_rebaixa(tmp_path: Path, catalogo: sqlite3.Connection) -> None:
    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    indexar([n], bruto=bruto, conexao=catalogo)

    # segunda passada com o texto vazio: simula a fonte devolvendo menos
    gravar_bruto(bruto, n, texto="")
    indexar([n], bruto=bruto, conexao=catalogo)

    (normas,) = catalogo.execute("SELECT count(*) FROM normas").fetchone()
    (artigos,) = catalogo.execute("SELECT artigos FROM normas").fetchone()
    (linhas_artigo,) = catalogo.execute("SELECT count(*) FROM artigos").fetchone()
    assert normas == 1
    assert artigos == 2, "a contagem de artigos não pode ser rebaixada por uma coleta pior"
    assert linhas_artigo == 2


def test_artigo_inteiro_fica_recuperavel_por_id(
    tmp_path: Path, catalogo: sqlite3.Connection
) -> None:
    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    indexar([n], bruto=bruto, conexao=catalogo)

    artigo = catalogo.execute(
        "SELECT * FROM artigos WHERE id = ?", (f"{n.id_norma}::art-1",)
    ).fetchone()
    assert artigo["artigo"] == "Art. 1º"
    assert artigo["capitulo"] == "CAPÍTULO I — DO OBJETO"
    assert "auditoria" in artigo["texto"], "o pai carrega o § junto com o caput"


def test_norma_sem_bruto_e_relatada(tmp_path: Path, catalogo: sqlite3.Connection) -> None:
    relatorio = indexar([norma()], bruto=tmp_path / "vazio", conexao=catalogo)
    assert relatorio.sem_texto == ["resolucao-cmn-4893-2021"]
    assert relatorio.normas == 0


def test_consulta_de_metadado_e_sql_e_nao_busca(
    tmp_path: Path, catalogo: sqlite3.Connection
) -> None:
    bruto = tmp_path / "bruto"
    vigente, revogada = norma("4893"), norma("4658")
    gravar_bruto(bruto, vigente)
    gravar_bruto(bruto, revogada, revogado=True)
    indexar([vigente, revogada], bruto=bruto, conexao=catalogo)

    linhas = catalogo.execute(
        "SELECT id_norma FROM normas WHERE tema = ? AND status_vigencia = 'vigente'",
        ("computacao_em_nuvem",),
    ).fetchall()
    assert [linha["id_norma"] for linha in linhas] == ["resolucao-cmn-4893-2021"]


def test_bm25_e_gravado_com_ids_alinhados(tmp_path: Path, catalogo: sqlite3.Connection) -> None:
    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    destino = tmp_path / "indices" / "bm25.json"

    relatorio = indexar([n], bruto=bruto, conexao=catalogo, caminho_bm25=destino)

    payload = json.loads(destino.read_text(encoding="utf-8"))
    assert len(payload["ids"]) == len(payload["tokens"]) == relatorio.chunks
    assert all(payload["tokens"])


def test_tokenizacao_preserva_o_numero_da_norma() -> None:
    tokens = tokenizar("Resolução nº 4.658, de 2018 — Art. 3º")
    assert "4658" in tokens, "sem o número inteiro o BM25 não distingue 4.658 de 4.568"
    assert "resolucao" in tokens
    assert "4568" not in tokens


def test_indexacao_densa_usa_upsert_com_id_deterministico(
    tmp_path: Path, catalogo: sqlite3.Connection
) -> None:
    class ColecaoFalsa:
        def __init__(self) -> None:
            self.chamadas: list[dict] = []

        def upsert(self, **kwargs) -> None:
            self.chamadas.append(kwargs)

    class EmbedderFalso:
        def embed(self, textos):
            return [_Vetor() for _ in textos]

    class _Vetor:
        def tolist(self) -> list[float]:
            return [0.1, 0.2]

    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    colecao = ColecaoFalsa()

    indexar([n], bruto=bruto, conexao=catalogo, colecao=colecao, embedder=EmbedderFalso())
    indexar([n], bruto=bruto, conexao=catalogo, colecao=colecao, embedder=EmbedderFalso())

    assert colecao.chamadas[0]["ids"] == colecao.chamadas[1]["ids"]
    assert colecao.chamadas[0]["metadatas"][0]["artigo"] == "Art. 1º"
    assert colecao.chamadas[0]["metadatas"][0]["revogada"] is False
