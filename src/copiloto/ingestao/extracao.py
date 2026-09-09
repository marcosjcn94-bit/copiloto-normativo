"""HTML de normativo do BCB -> blocos com a estrutura normativa preservada.

O documento do BCB chega como HTML de editor de texto: cada unidade normativa
(caput de artigo, parágrafo, inciso, alínea) é um `<p>`. Este módulo transforma
isso em blocos rotulados, porque sem o rótulo do artigo a citação é impossível e
o projeto inteiro cai (§4.2.1 do briefing).

Duas armadilhas do formato tratadas aqui:

* **Texto riscado.** Alteração de redação é publicada como `<s>...</s>`: a
  redação antiga continua no documento. Indexar isso faria o copiloto citar
  redação revogada, que é o pior erro possível neste domínio.
* **Encoding.** A acentuação é verificada por código (`acentuacao_intacta`),
  não por inspeção visual.

Este módulo não faz rede e não conhece o formato da API — recebe HTML e devolve
blocos.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Literal

logger = logging.getLogger(__name__)

TipoBloco = Literal["capitulo", "secao", "artigo", "paragrafo", "inciso", "alinea", "outro"]

# Tags que encerram uma unidade de texto no HTML do BCB.
_TAGS_DE_BLOCO = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
)
# Tags que marcam redação riscada (revogada ou substituída).
_TAGS_RISCADAS = frozenset({"s", "strike", "del"})

# Sequências que aparecem quando UTF-8 foi lido como latin-1 em algum ponto do caminho.
_MOJIBAKE = ("Ã§", "Ã£", "Ã©", "Ã¡", "Ãµ", "Ãª", "Ã³", "Ã\xad", "Ã‡", "Ã‰")

_RE_ARTIGO = re.compile(r"^Art\.?\s*(\d+)\s*(?:[ºo°]|\.|\b)", re.IGNORECASE)
_RE_PARAGRAFO_UNICO = re.compile(r"^Par[áa]grafo\s+[úu]nico", re.IGNORECASE)
_RE_PARAGRAFO = re.compile(r"^§\s*(\d+)\s*(?:[ºo°]|\.|\b)")
_RE_INCISO = re.compile(r"^(X{0,3}(?:IX|IV|V?I{0,3}))\s*[-–—]\s")
_RE_ALINEA = re.compile(r"^([a-z])\)\s")
_RE_CAPITULO = re.compile(r"^CAP[ÍI]TULO\s+([IVXLC]+)", re.IGNORECASE)
_RE_SECAO = re.compile(r"^Se[çc][ãa]o\s+([IVXLC]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Bloco:
    """Uma unidade normativa já classificada.

    `rotulo` é normalizado (`Art. 3º`, `§ 1º`, `III`, `a)`) para que a citação
    montada na Fase 5 não dependa de como o editor de texto formatou o original.
    """

    tipo: TipoBloco
    rotulo: str
    texto: str
    riscado: bool = False
    numero_artigo: int | None = None


class _ColetorDeLinhas(HTMLParser):
    """Achata o HTML em linhas, preservando a informação de texto riscado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.linhas: list[tuple[str, bool]] = []
        self._pedacos: list[str] = []
        self._riscado_no_bloco = False
        self._profundidade_riscada = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:  # noqa: ARG002
        if tag in _TAGS_RISCADAS:
            self._profundidade_riscada += 1
        if tag in _TAGS_DE_BLOCO:
            self._fechar_linha()

    def handle_endtag(self, tag: str) -> None:
        if tag in _TAGS_RISCADAS and self._profundidade_riscada > 0:
            self._profundidade_riscada -= 1
        if tag in _TAGS_DE_BLOCO:
            self._fechar_linha()

    def handle_data(self, data: str) -> None:
        if not data.strip():
            self._pedacos.append(" ")
            return
        if self._profundidade_riscada:
            self._riscado_no_bloco = True
            return  # a redação substituída não entra no texto vivo
        self._pedacos.append(data)

    def _fechar_linha(self) -> None:
        # `texto` guarda só o que não está riscado; a linha é marcada como
        # riscada quando o tachado consumiu tudo — é assim que uma substituição
        # de trecho preserva o restante do dispositivo em vez de descartá-lo.
        texto = normalizar_texto("".join(self._pedacos))
        if texto:
            self.linhas.append((texto, False))
        elif self._riscado_no_bloco:
            self.linhas.append(("", True))
        self._pedacos.clear()
        self._riscado_no_bloco = False

    def close(self) -> None:
        super().close()
        self._fechar_linha()


def normalizar_texto(bruto: str) -> str:
    """Desfaz entidades, colapsa espaços e normaliza em NFC.

    NFC importa: o `ç` composto (`c` + U+0327) e o `ç` precomposto são strings
    diferentes para o BM25 da Fase 3.
    """
    texto = unescape(bruto).replace("\xa0", " ").replace("​", "")
    texto = unicodedata.normalize("NFC", texto)
    return re.sub(r"\s+", " ", texto).strip()


def acentuacao_intacta(texto: str) -> bool:
    """Falso se o texto trouxer caractere de substituição ou mojibake latin-1.

    Critério de pronto da Fase 1: acentuação quebrada para o pipeline antes de
    qualquer indexação.
    """
    if "�" in texto:
        return False
    return not any(marca in texto for marca in _MOJIBAKE)


def classificar_linha(linha: str) -> tuple[TipoBloco, str, int | None]:
    """Devolve (tipo, rótulo normalizado, número do artigo) de uma linha."""
    if casou := _RE_ARTIGO.match(linha):
        numero = int(casou.group(1))
        return "artigo", f"Art. {numero}º", numero
    if _RE_PARAGRAFO_UNICO.match(linha):
        return "paragrafo", "Parágrafo único", None
    if casou := _RE_PARAGRAFO.match(linha):
        return "paragrafo", f"§ {int(casou.group(1))}º", None
    if casou := _RE_INCISO.match(linha):
        return "inciso", casou.group(1).upper(), None
    if casou := _RE_ALINEA.match(linha):
        return "alinea", f"{casou.group(1)})", None
    if casou := _RE_CAPITULO.match(linha):
        return "capitulo", f"Capítulo {casou.group(1).upper()}", None
    if casou := _RE_SECAO.match(linha):
        return "secao", f"Seção {casou.group(1).upper()}", None
    return "outro", "", None


def extrair_blocos(html_bruto: str) -> list[Bloco]:
    """HTML do normativo -> blocos classificados, na ordem do documento."""
    coletor = _ColetorDeLinhas()
    coletor.feed(html_bruto)
    coletor.close()

    blocos: list[Bloco] = []
    for linha, riscado in coletor.linhas:
        tipo, rotulo, numero = classificar_linha(linha)
        blocos.append(
            Bloco(tipo=tipo, rotulo=rotulo, texto=linha, riscado=riscado, numero_artigo=numero)
        )

    riscados = sum(1 for bloco in blocos if bloco.riscado)
    logger.info("extracao concluida", extra={"blocos": len(blocos), "blocos_riscados": riscados})
    return blocos
