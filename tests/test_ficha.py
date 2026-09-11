"""Fase 9 — `config/governanca.yaml` tipado (§16.2 do briefing).

A ficha é contrato, não documento: campo extra ou obrigatório ausente é erro
de validação, a mesma disciplina de `extra='forbid'` que `tools/schemas.py`
já exige das tools.
"""

from __future__ import annotations

import yaml
from pydantic import ValidationError

from copiloto.governanca.ficha import CAMINHO_PADRAO, FichaDoSistema, carregar_ficha


def test_ficha_padrao_carrega_e_valida() -> None:
    ficha = carregar_ficha()

    assert ficha.identificacao.id == "copiloto-normativo-bcb"
    assert ficha.autonomia.nivel == 2
    assert {m.id_sistema for m in ficha.modelos} >= {"groq", "eval-judge/camada-3"}


def test_campo_extra_e_recusado() -> None:
    bruto = yaml.safe_load(CAMINHO_PADRAO.read_text(encoding="utf-8"))
    bruto["campo_que_nao_existe"] = True

    try:
        FichaDoSistema.model_validate(bruto)
    except ValidationError:
        pass
    else:
        raise AssertionError("campo extra deveria ter sido recusado")


def test_campo_obrigatorio_ausente_e_recusado() -> None:
    bruto = yaml.safe_load(CAMINHO_PADRAO.read_text(encoding="utf-8"))
    del bruto["autonomia"]["nivel"]

    try:
        FichaDoSistema.model_validate(bruto)
    except ValidationError:
        pass
    else:
        raise AssertionError("campo obrigatório ausente deveria ter sido recusado")
