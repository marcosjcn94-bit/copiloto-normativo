"""Integridade do gabarito.

Um `golden.jsonl` que referencia artigo inexistente produz recall artificialmente
baixo e manda consertar o retriever quando o defeito está no gabarito. Estes
testes não medem qualidade nenhuma — garantem que a régua não está torta.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
GOLDEN = RAIZ / "evals" / "golden.jsonl"
CATALOGO = RAIZ / "data" / "catalogo.sqlite"

TIPOS = {"rag", "nao_sei", "sql"}


@pytest.fixture(scope="module")
def registros() -> list[dict]:
    linhas = [linha for linha in GOLDEN.read_text(encoding="utf-8").splitlines() if linha.strip()]
    return [json.loads(linha) for linha in linhas]


def test_tem_quarenta_perguntas(registros) -> None:
    assert len(registros) == 40


def test_ids_sao_unicos(registros) -> None:
    ids = [r["id"] for r in registros]
    assert len(set(ids)) == len(ids)


def test_composicao_exigida_pelo_briefing(registros) -> None:
    """§9: 5 perguntas de 'não sei', 3 de norma revogada e 3 que pedem SQL.

    São esses casos que separam um agente de um chatbot — se alguém podá-los
    para o número subir, o teste avisa.
    """
    tipos = Counter(r["tipo"] for r in registros)
    assert set(tipos) <= TIPOS
    assert tipos["nao_sei"] == 5
    assert tipos["sql"] == 3
    assert sum(1 for r in registros if r["grupo"] == "revogada") == 3


def test_so_perguntas_rag_tem_artigo_esperado(registros) -> None:
    for registro in registros:
        tem_esperado = bool(registro["esperado"])
        assert tem_esperado == (registro["tipo"] == "rag"), registro["id"]


def test_toda_referencia_existe_no_catalogo(registros) -> None:
    """A âncora do gabarito é o catálogo, não a memória de quem o escreveu."""
    if not CATALOGO.exists():
        pytest.skip("catálogo ausente — rodar a indexação da Fase 2")
    conexao = sqlite3.connect(CATALOGO)
    existentes = {
        (linha[0], linha[1])
        for linha in conexao.execute("SELECT id_norma, numero_artigo FROM artigos")
    }
    conexao.close()

    quebradas = [
        (registro["id"], esperado["id_norma"], esperado["numero_artigo"])
        for registro in registros
        for esperado in registro["esperado"]
        if (esperado["id_norma"], int(esperado["numero_artigo"])) not in existentes
    ]
    assert not quebradas, f"referências sem artigo correspondente: {quebradas}"


def test_perguntas_revogadas_apontam_para_normas_revogadas(registros) -> None:
    if not CATALOGO.exists():
        pytest.skip("catálogo ausente — rodar a indexação da Fase 2")
    conexao = sqlite3.connect(CATALOGO)
    status = dict(conexao.execute("SELECT id_norma, status_vigencia FROM normas"))
    conexao.close()

    for registro in registros:
        if registro["grupo"] != "revogada":
            continue
        for esperado in registro["esperado"]:
            assert status[esperado["id_norma"]] == "revogada", registro["id"]
