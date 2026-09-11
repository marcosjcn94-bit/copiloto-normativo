"""Guardrails de entrada e de saída — §8 do briefing.

**Entrada.** Neutraliza PII antes que a pergunta entre no estado (e, na Fase 7,
no trace do Langfuse) e recusa tentativa de sobrescrever a instrução do sistema.
O corpus é público, então a injeção indireta aqui é baixa; o filtro fica porque
o custo é uma regex e o risco de não ter é uma pergunta de cliente virando dado
pessoal em log de terceiro.

**Saída — o diferencial do projeto.** `validar_resposta` extrai toda citação da
resposta e confere, por código, que ela saiu dos trechos recuperados *naquela
execução*. Três decisões sustentam o desenho:

* **A comparação é estrutural, não textual.** Citação vira `(tipo, número, ano,
  artigo)`. O modelo escrever `Resolução CMN 4.893/2021, art. 3` em vez do
  formato canônico não pode virar reprovação — errar o artigo, sim.
* **Ausência de citação também reprova.** Uma resposta afirmativa sem fonte é
  exatamente o que o projeto promete não fazer. A única exceção é a recusa
  honesta (`SEM_BASE_NORMATIVA`), que é resposta legítima.
* **Norma revogada exige aviso.** Está no §8 como teste, não como boa intenção:
  citou trecho com `revogada`, a resposta tem de dizer isso.

Nada aqui pede nada ao modelo. Prompt é sugestão; isto é verificação.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from copiloto.tools.schemas import PADRAO_TIPOS_NORMA

SEM_BASE_NORMATIVA = "Não encontrei base normativa para isso."

# --- entrada -----------------------------------------------------------------

# PII que aparece numa pergunta de atendimento. O CPF cobre a forma pontuada e a
# crua; o telefone cobre fixo e celular, com e sem DDD entre parênteses.
_PII: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("cpf", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF]"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("telefone", re.compile(r"(?<!\d)(?:\(?\d{2}\)?[\s-]?)?\d{4,5}-?\d{4}(?!\d)"), "[TELEFONE]"),
)

# Frases que só existem para trocar a instrução do sistema. A lista é curta de
# propósito: reconhecer padrão de comando, não vigiar vocabulário.
_INJECAO = re.compile(
    r"(ignore|esque[çc]a|desconsidere)\s+(as\s+|o\s+|todas\s+as\s+)?"
    r"(instru[çc][õo]es|regras|tudo)"
    r"|voc[êe]\s+(agora\s+)?[ée]\s+(um|uma)\s"
    r"|(system|developer)\s*prompt"
    r"|revele?\s+(o\s+)?(seu\s+)?prompt",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Saneamento:
    """A pergunta como ela entra no estado, mais o que foi encontrado nela."""

    texto: str
    achados: tuple[str, ...] = ()
    bloqueado: bool = False
    motivo: str = ""


def sanear_pergunta(texto: str) -> Saneamento:
    """Neutraliza PII e recusa tentativa de injeção.

    A ordem é deliberada: neutraliza primeiro, bloqueia depois. Uma pergunta
    bloqueada continua sendo gravada no estado e no trace, então ela também não
    pode carregar CPF — bloquear antes de limpar guardaria o dado pessoal do
    único caso em que ninguém vai reler o texto para conferir.
    """
    saneado = texto
    achados: list[str] = []
    for nome, padrao, mascara in _PII:
        saneado, trocas = padrao.subn(mascara, saneado)
        if trocas:
            achados.append(nome)

    if _INJECAO.search(saneado):
        return Saneamento(
            texto=saneado,
            achados=tuple(achados),
            bloqueado=True,
            motivo="tentativa_de_injecao",
        )
    return Saneamento(texto=saneado, achados=tuple(achados))


# --- saída: extração de citação ----------------------------------------------

_CITACAO = re.compile(
    rf"(?P<tipo>{PADRAO_TIPOS_NORMA})"
    r"\s*(?:n[º°o]\.?\s*)?"
    # O número da norma vem em duas grafias no corpus real: com separador de
    # milhar (`4.893`) e sem (`5274`, `3979`). A alternativa com ponto vem
    # primeiro porque `\d{1,6}` casaria `4` e pararia antes do separador.
    #
    # **Este foi o defeito mais caro da Fase 7.** A versão anterior aceitava só
    # `\d{1,3}(\.\d{3})*`, que casa `85` e `4.893` mas para no primeiro dígito de
    # `5274` — e aí a citação inteira não casava. Como as tools devolvem o número
    # cru do catálogo, toda resposta sobre Circular nº 3979 ou Resolução CMN nº
    # 5274 era reprovada como `sem_citacao`, o agente gastava as duas tentativas
    # e terminava dizendo que não encontrou base normativa. Uma resposta correta
    # virava recusa, e nenhum teste via: as fixtures usavam `85` e `4.893`.
    # Quem pegou foi a camada 1 de `evals/rodar.py` rodando sobre o corpus real.
    r"(?P<numero>\d{1,3}(?:\.\d{3})+|\d{1,6})"
    r"(?:\s*(?:,\s*de\s*|/)\s*(?P<ano>\d{4}))?"
    r"\s*,?\s*art(?:igo)?\.?\s*"
    r"(?P<artigo>\d{1,3})[º°]?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Citacao:
    """Uma citação já normalizada, pronta para comparar com o que foi recuperado."""

    tipo: str
    numero: str
    ano: str
    artigo: str
    texto: str

    def combina(self, outra: Citacao) -> bool:
        """Mesma norma e mesmo artigo.

        O ano só decide quando os dois lados o trazem — o modelo pode omiti-lo, e
        omissão não é erro de fato. O tipo casa por continência, para tolerar
        `Resolução 85` contra `Resolução BCB nº 85`; `bcb` contra `cmn` não casa,
        que é justamente o par que precisa ser distinguido.
        """
        if self.numero != outra.numero or self.artigo != outra.artigo:
            return False
        if self.ano and outra.ano and self.ano != outra.ano:
            return False
        return self.tipo in outra.tipo or outra.tipo in self.tipo


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _citacao_de(achado: re.Match[str]) -> Citacao:
    return Citacao(
        tipo=re.sub(r"\s+", " ", _sem_acento(achado["tipo"]).strip().lower()),
        numero=achado["numero"].replace(".", ""),
        ano=achado["ano"] or "",
        artigo=achado["artigo"].lstrip("0") or "0",
        texto=re.sub(r"\s+", " ", achado.group(0).strip()),
    )


def extrair_citacoes(texto: str) -> tuple[Citacao, ...]:
    """Toda citação de norma e artigo presente no texto, na ordem em que aparece."""
    return tuple(_citacao_de(achado) for achado in _CITACAO.finditer(texto))


# --- saída: veredito ---------------------------------------------------------


class Fonte(Protocol):
    """O mínimo que o validador usa de um trecho recuperado."""

    citacao: str
    revogada: bool


_AVISO_DE_REVOGACAO = re.compile(r"revogad[ao]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Veredito:
    """O que o grafo consulta para decidir entre responder e tentar de novo."""

    aprovada: bool
    motivo: str = ""
    orfas: tuple[str, ...] = ()
    citadas: tuple[str, ...] = ()

    @property
    def instrucao_de_correcao(self) -> str:
        """A mensagem que volta ao modelo. Diz o que reprovou e o que fazer."""
        if self.aprovada:
            return ""
        if self.motivo == "citacao_orfa":
            return (
                "As citações "
                + ", ".join(self.orfas)
                + " não aparecem nos trechos recuperados nesta execução. "
                "Reescreva a resposta usando apenas as citações exatamente como "
                "vieram de buscar_normativo, ou responda que não encontrou base normativa."
            )
        if self.motivo == "sem_citacao":
            return (
                "A resposta não cita norma e artigo. Reescreva citando os trechos "
                "recuperados, ou responda que não encontrou base normativa."
            )
        return (
            "A resposta cita norma revogada sem avisar. Reescreva dizendo "
            "explicitamente que a norma está revogada."
        )


def validar_resposta(resposta: str, trechos: Sequence[Fonte]) -> Veredito:
    """Confere, por código, que cada citação da resposta saiu dos trechos desta execução."""
    recuperadas = tuple(
        citacao for trecho in trechos for citacao in extrair_citacoes(trecho.citacao)
    )
    citadas = extrair_citacoes(resposta)

    if not citadas:
        # Recusa honesta é resposta legítima — e é a única saída sem citação que o
        # grafo emite, porque é a frase que ele mesmo escreve no fallback.
        if resposta.strip() == SEM_BASE_NORMATIVA or not trechos:
            return Veredito(aprovada=True)
        return Veredito(aprovada=False, motivo="sem_citacao")

    orfas = tuple(c.texto for c in citadas if not any(c.combina(r) for r in recuperadas))
    if orfas:
        return Veredito(aprovada=False, motivo="citacao_orfa", orfas=orfas)

    revogadas = tuple(
        citacao
        for trecho in trechos
        if trecho.revogada
        for citacao in extrair_citacoes(trecho.citacao)
    )
    citou_revogada = any(any(c.combina(r) for r in revogadas) for c in citadas)
    if citou_revogada and not _AVISO_DE_REVOGACAO.search(resposta):
        return Veredito(aprovada=False, motivo="aviso_de_vigencia_ausente")

    return Veredito(aprovada=True, citadas=tuple(c.texto for c in citadas))
