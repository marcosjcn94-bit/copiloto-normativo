"""Recuperação híbrida: denso + esparso -> RRF -> rerank -> artigo citável."""

from copiloto.recuperacao.retriever import (
    MODOS,
    Modo,
    ParametrosRecuperacao,
    Recuperador,
    Trecho,
    carregar_parametros,
)

__all__ = [
    "MODOS",
    "Modo",
    "ParametrosRecuperacao",
    "Recuperador",
    "Trecho",
    "carregar_parametros",
]
