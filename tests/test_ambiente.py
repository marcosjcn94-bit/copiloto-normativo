"""Critério de pronto da Fase 0: a stack importa e o Python é o suportado."""

import sys

import pytest


def test_versao_do_python_suportada() -> None:
    assert (3, 11) <= sys.version_info < (3, 14)


@pytest.mark.parametrize("modulo", ["langgraph", "fastembed", "flashrank"])
def test_stack_importa(modulo: str) -> None:
    __import__(modulo)


@pytest.mark.parametrize("modulo", ["chromadb", "rank_bm25", "fastapi", "httpx", "pydantic"])
def test_demais_dependencias_importam(modulo: str) -> None:
    __import__(modulo)
