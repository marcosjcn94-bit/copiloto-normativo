"""O coração técnico da Fase 9: a ficha declara, o código é (§16.3 do briefing).

Quatro verificações, cada uma comparando um campo de `governanca.yaml` contra
a fonte real de que ele fala. `parametros.toml` **é**; a ficha **declara**.
Onde os dois discordam, quem está errado é a ficha — e é por isso que toda
função aqui devolve `Divergencia`, nunca corrige nada: corrigir a ficha
automaticamente esconderia exatamente o desvio que esta camada existe para
expor.

`GET /governanca/coerencia` responde 200 com lista vazia ou 409 com as
divergências; `tests/test_coerencia.py` reprova o CI em qualquer uma. É a
prova viva do §16: mudar `max_passos` em `parametros.toml` sem atualizar a
ficha tem que deixar isto vermelho.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from copiloto.governanca.ficha import FichaDoSistema, carregar_ficha
from copiloto.governanca.inventario import frota_real

RAIZ_PADRAO = Path(__file__).resolve().parents[3]
TOML_PADRAO = RAIZ_PADRAO / "config" / "parametros.toml"

# Tempo de sobra para um teste de guardrail (regex pura, sem rede) mais o
# custo de subir outro processo Python. Um controle preso além disso é
# sintoma melhor relatado como divergência do que como travamento do
# endpoint de coerência.
TIMEOUT_TESTE = 30.0


@dataclass(frozen=True, slots=True)
class Divergencia:
    """Um campo em que a ficha e o código discordam."""

    verificacao: str
    campo: str
    declarado: object
    real: object

    def como_dicionario(self) -> dict[str, object]:
        return {
            "verificacao": self.verificacao,
            "campo": self.campo,
            "declarado": self.declarado,
            "real": self.real,
        }


def _toml(caminho: Path) -> dict:
    return tomllib.loads(caminho.read_text(encoding="utf-8"))


def verificar_limites_operacionais(
    ficha: FichaDoSistema, toml_caminho: Path = TOML_PADRAO
) -> list[Divergencia]:
    """`limites_operacionais` contra `[grafo]` e `[llm]` de `parametros.toml`."""
    dados = _toml(toml_caminho)
    reais = {
        "max_passos": dados["grafo"]["max_passos"],
        "max_autocorrecao": dados["grafo"]["max_autocorrecao"],
        "max_tentativas_resposta": dados["grafo"]["max_tentativas_resposta"],
        "temperatura": dados["llm"]["temperatura"],
        "max_tokens_ctx": dados["llm"]["max_tokens_ctx"],
    }
    declarados = ficha.limites_operacionais.model_dump()
    return [
        Divergencia("limites_operacionais", campo, declarados[campo], reais[campo])
        for campo in reais
        if declarados[campo] != reais[campo]
    ]


def verificar_thresholds_de_eval(
    ficha: FichaDoSistema, toml_caminho: Path = TOML_PADRAO
) -> list[Divergencia]:
    """`metricas` contra `[evals]` de `parametros.toml`."""
    reais = _toml(toml_caminho)["evals"]
    declarados = ficha.metricas.model_dump(exclude={"onde_medido"})
    return [
        Divergencia("thresholds_de_eval", campo, declarados[campo], reais[campo])
        for campo in reais
        if declarados[campo] != reais[campo]
    ]


def verificar_modelos_inventariados(
    ficha: FichaDoSistema, raiz: Path = RAIZ_PADRAO
) -> list[Divergencia]:
    """`modelos[]` contra a frota que `inventario.py` varre no código.

    Nos dois sentidos: todo modelo `implementado: true` na ficha tem de
    aparecer na varredura, e todo sistema que a varredura encontra tem de
    estar declarado — um provedor novo em `llm/` sem entrada na ficha é
    exatamente o tipo de deriva silenciosa que esta verificação existe para
    pegar.
    """
    declarados = {m.id_sistema for m in ficha.modelos if m.implementado}
    reais = {s.id_sistema for s in frota_real(raiz)}

    divergencias: list[Divergencia] = []
    for faltando in sorted(declarados - reais):
        divergencias.append(
            Divergencia(
                "modelos_inventariados", faltando, "implementado: true", "ausente na varredura"
            )
        )
    for sobrando in sorted(reais - declarados):
        divergencias.append(
            Divergencia(
                "modelos_inventariados",
                sobrando,
                "ausente ou implementado: false",
                "presente na varredura",
            )
        )
    return divergencias


def _teste_passa(nodeid: str, raiz: Path) -> tuple[bool, str]:
    """Roda um teste isolado e diz se passou. `False` com motivo quando não."""
    caminho = nodeid.split("::", 1)[0]
    if not (raiz / caminho).exists():
        return False, "arquivo de teste não encontrado"
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "pytest", nodeid, "-q"],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_TESTE,
        )
    except subprocess.TimeoutExpired:
        return False, f"teste não terminou em {TIMEOUT_TESTE:.0f}s"
    if resultado.returncode != 0:
        return False, "teste reprovou ou não foi coletado"
    return True, ""


def verificar_controles_com_prova(
    ficha: FichaDoSistema, raiz: Path = RAIZ_PADRAO
) -> list[Divergencia]:
    """`controles[].teste` existe e passa. Controle sem prova é afirmação."""
    divergencias: list[Divergencia] = []
    for controle in ficha.controles:
        passou, motivo = _teste_passa(controle.teste, raiz)
        if not passou:
            divergencias.append(
                Divergencia("controles_com_prova", controle.id, controle.teste, motivo)
            )
    return divergencias


def verificar(
    ficha: FichaDoSistema | None = None,
    *,
    raiz: Path = RAIZ_PADRAO,
    toml_caminho: Path = TOML_PADRAO,
) -> tuple[Divergencia, ...]:
    """Todas as quatro verificações do §16.3. Lista vazia é o único veredito verde."""
    f = ficha or carregar_ficha()
    divergencias: list[Divergencia] = [
        *verificar_limites_operacionais(f, toml_caminho),
        *verificar_thresholds_de_eval(f, toml_caminho),
        *verificar_modelos_inventariados(f, raiz),
        *verificar_controles_com_prova(f, raiz),
    ]
    return tuple(divergencias)
