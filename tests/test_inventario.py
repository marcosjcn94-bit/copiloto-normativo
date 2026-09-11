"""Fase 9 — a frota varrida do repositório (§16.1 do briefing).

`frota_real` não pode ter entrada fictícia: os testes aqui provam que ela só
acha o que o código realmente implementa, e que `azure_openai`/`ollama` —
previstos e não implementados — não aparecem nela mesmo estando citados em
`llm/provedor.py` e em `config/governanca.yaml`.
"""

from __future__ import annotations

from copiloto.governanca.ficha import ModeloDeIA, carregar_ficha
from copiloto.governanca.inventario import frota_prevista, frota_real, inventario


def test_frota_real_encontra_groq_e_o_juiz() -> None:
    sistemas = {s.id_sistema: s for s in frota_real()}

    assert sistemas["groq"].papel == "primario"
    assert sistemas["groq"].implementado is True
    assert sistemas["eval-judge/camada-3"].papel == "juiz"
    assert sistemas["eval-judge/camada-3"].implementado is True


def test_azure_openai_e_ollama_nao_aparecem_na_frota_real() -> None:
    ids = {s.id_sistema for s in frota_real()}

    assert "azure_openai" not in ids
    assert "ollama" not in ids


def test_frota_prevista_reflete_so_o_que_a_ficha_marca_nao_implementado() -> None:
    modelos = (
        ModeloDeIA(
            id_sistema="groq",
            provedor="groq",
            modelo="x",
            fornecedor="Groq",
            hospedagem="cloud_terceiro",
            papel="primario",
            implementado=True,
        ),
        ModeloDeIA(
            id_sistema="ollama",
            provedor="ollama",
            modelo="qwen2.5:3b",
            fornecedor="Ollama",
            hospedagem="local",
            papel="fallback",
            implementado=False,
        ),
    )

    prevista = frota_prevista(modelos)

    assert [s.id_sistema for s in prevista] == ["ollama"]
    assert prevista[0].implementado is False


def test_inventario_da_ficha_real_lista_dois_implementados_e_dois_previstos() -> None:
    ficha = carregar_ficha()

    resultado = inventario(modelos_da_ficha=tuple(ficha.modelos))

    assert resultado["total_implementados"] == 2
    assert resultado["total_previstos"] == 2
