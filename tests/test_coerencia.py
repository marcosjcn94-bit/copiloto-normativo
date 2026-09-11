"""Fase 9 — a prova viva do §16.3: a ficha declara, o código é.

`test_ficha_atual_e_coerente_com_o_codigo` é o que fica vermelho quando
alguém muda `parametros.toml` sem atualizar `config/governanca.yaml` — é a
demonstração do README (§14, passo 9): mudar `max_passos`, ver isto reprovar,
reverter, ver isto passar de novo. Os demais testes isolam cada uma das
quatro verificações do §16.3 contra um TOML ou uma ficha deliberadamente
divergente, para que a razão da reprovação nunca dependa de coincidência com
o estado real do repositório.
"""

from __future__ import annotations

from pathlib import Path

from copiloto.governanca.coerencia import (
    verificar,
    verificar_controles_com_prova,
    verificar_limites_operacionais,
    verificar_modelos_inventariados,
    verificar_thresholds_de_eval,
)
from copiloto.governanca.ficha import Controle, ModeloDeIA, carregar_ficha

TOML_BASE_GRAFO_LLM = """
[grafo]
max_passos = 8
max_autocorrecao = 2
max_tentativas_resposta = 2

[llm]
temperatura = 0.0
max_tokens_ctx = 8000

[evals]
faithfulness_min = 0.95
relevancia_min = 0.85
erro_tool_max = 0.02
recall_5_min = 0.80
"""


def _toml_com(tmp_path: Path, alteracao: tuple[str, str]) -> Path:
    de, para = alteracao
    caminho = tmp_path / "parametros.toml"
    caminho.write_text(TOML_BASE_GRAFO_LLM.replace(de, para), encoding="utf-8")
    return caminho


def test_ficha_atual_e_coerente_com_o_codigo() -> None:
    """O baseline verde. Se isto reprovar, a ficha ou o TOML saiu de sincronia."""
    assert verificar() == ()


def test_limites_operacionais_diverge_quando_toml_muda(tmp_path: Path) -> None:
    ficha = carregar_ficha()
    toml = _toml_com(tmp_path, ("max_passos = 8", "max_passos = 9"))

    divergencias = verificar_limites_operacionais(ficha, toml)

    assert len(divergencias) == 1
    assert divergencias[0].campo == "max_passos"
    assert divergencias[0].declarado == 8
    assert divergencias[0].real == 9


def test_thresholds_de_eval_diverge_quando_toml_muda(tmp_path: Path) -> None:
    ficha = carregar_ficha()
    toml = _toml_com(tmp_path, ("recall_5_min = 0.80", "recall_5_min = 0.90"))

    divergencias = verificar_thresholds_de_eval(ficha, toml)

    assert len(divergencias) == 1
    assert divergencias[0].campo == "recall_5_min"


def test_modelos_inventariados_acusa_provedor_real_nao_declarado() -> None:
    ficha = carregar_ficha()
    # Remove `groq` da lista de implementados: o código continua provendo
    # `groq`, então a varredura deveria acusar "sobrando".
    sem_groq = tuple(m for m in ficha.modelos if m.id_sistema != "groq")
    ficha_mutada = ficha.model_copy(update={"modelos": sem_groq})

    divergencias = verificar_modelos_inventariados(ficha_mutada)

    assert any(d.campo == "groq" and d.real == "presente na varredura" for d in divergencias)


def test_modelos_inventariados_acusa_declaracao_sem_implementacao_real() -> None:
    ficha = carregar_ficha()
    fantasma = ModeloDeIA(
        id_sistema="provedor-que-nao-existe",
        provedor="x",
        modelo="x",
        fornecedor="x",
        hospedagem="cloud_terceiro",
        papel="fallback",
        implementado=True,
    )
    ficha_mutada = ficha.model_copy(update={"modelos": (*ficha.modelos, fantasma)})

    divergencias = verificar_modelos_inventariados(ficha_mutada)

    assert any(d.campo == "provedor-que-nao-existe" for d in divergencias)


def test_controle_com_teste_inexistente_diverge() -> None:
    ficha = carregar_ficha()
    controle_quebrado = Controle(
        id="entrada.inventado",
        tipo="entrada",
        deterministico=True,
        teste="tests/test_arquivo_que_nao_existe.py::test_nada",
    )
    ficha_mutada = ficha.model_copy(update={"controles": (controle_quebrado,)})

    divergencias = verificar_controles_com_prova(ficha_mutada)

    assert len(divergencias) == 1
    assert divergencias[0].campo == "entrada.inventado"
    assert "não encontrado" in divergencias[0].real
