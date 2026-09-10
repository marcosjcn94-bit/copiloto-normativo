"""Observabilidade: o trace que torna o comportamento do agente auditável (Fase 7)."""

from copiloto.observabilidade.tracing import (
    Observacao,
    ProvedorRastreado,
    Rastreador,
    RastreadorLangfuse,
    RastreadorNulo,
    Sessao,
    TipoDeObservacao,
    abrir_rastreador,
    autenticar,
    mensagem_para_openai,
    sessao_nula,
    versao_da_ficha,
)

__all__ = [
    "Observacao",
    "ProvedorRastreado",
    "Rastreador",
    "RastreadorLangfuse",
    "RastreadorNulo",
    "Sessao",
    "TipoDeObservacao",
    "abrir_rastreador",
    "autenticar",
    "mensagem_para_openai",
    "sessao_nula",
    "versao_da_ficha",
]
