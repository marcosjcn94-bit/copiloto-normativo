"""Blocos extraídos -> chunk pai (artigo) e chunk filho (unidade).

Estratégia do §4.2.6 do briefing: **vetorizar o parágrafo, entregar o artigo**.
O filho é pequeno, então a busca é precisa; o pai é o artigo inteiro, então o
que chega ao LLM é coerente e citável.

Quebra por artigo, nunca por contagem cega de caracteres: todo chunk carrega o
artigo, o parágrafo e o inciso a que pertence, e é essa metadata que a Fase 5
usa para rejeitar resposta que cite artigo fora dos trechos recuperados.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from copiloto.ingestao.extracao import Bloco

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Norma:
    """Identificação da norma. O catálogo SQL da Fase 2 vira a fonte disso."""

    tipo: str
    numero: str
    ano: int
    revogada: bool = False

    @property
    def rotulo(self) -> str:
        return f"{self.tipo} nº {self.numero}, de {self.ano}"

    @property
    def id_norma(self) -> str:
        base = f"{self.tipo}-{self.numero}-{self.ano}"
        sem_acento = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


# Separador dos três níveis do id: `{id_norma}::art-{n}::u{ordem}`. O id é
# cunhado em `montar_chunks`, logo abaixo, e lido por `id_norma_do_chunk` — as
# duas pontas ficam neste arquivo de propósito, porque uma delas mudar sem a
# outra faria o filtro por norma calar em vez de falhar.
SEPARADOR_DE_ID = "::"


def id_norma_do_chunk(id_chunk: str) -> str:
    """A norma a que um chunk pertence, lida do próprio id.

    O índice esparso guarda id e tokens, nada mais: para restringir o BM25 a uma
    norma é preciso saber de qual norma cada id é, e perguntar isso ao vetorial
    custaria uma ida ao adapter por busca. O id já carrega a resposta.

    `tests/test_chunking.py` fecha o círculo: monta chunks de verdade e confere
    que esta função devolve o `id_norma` que o chunk declara no campo.
    """
    return id_chunk.split(SEPARADOR_DE_ID, 1)[0]


@dataclass(frozen=True, slots=True)
class ChunkPai:
    """Um artigo inteiro — é isto que o LLM recebe como contexto."""

    id: str
    id_norma: str
    norma: str
    artigo: str
    numero_artigo: int
    texto: str
    capitulo: str | None = None
    secao: str | None = None
    revogada: bool = False


@dataclass(frozen=True, slots=True)
class ChunkFilho:
    """Uma unidade normativa — é isto que vai para o índice vetorial."""

    id: str
    id_pai: str
    id_norma: str
    norma: str
    artigo: str
    numero_artigo: int
    rotulo: str
    texto: str
    texto_indexavel: str
    capitulo: str | None = None
    secao: str | None = None
    revogada: bool = False


@dataclass
class _ArtigoEmMontagem:
    numero: int
    artigo: str
    capitulo: str | None
    secao: str | None
    caput: str
    linhas: list[str] = field(default_factory=list)
    unidades: list[tuple[str, str]] = field(default_factory=list)


def _metadata_do_indice(norma: Norma, rotulo: str, caput: str, texto: str) -> str:
    """Texto que vai ao embedding: rótulo + caput + unidade.

    O inciso isolado ("III - manter registro...") não significa nada sem o caput
    que o rege, e o rótulo dá ao BM25 da Fase 3 o número da norma como termo.
    """
    partes = [f"{norma.rotulo} — {rotulo}"]
    if texto != caput:
        partes.append(caput)
    partes.append(texto)
    return "\n".join(partes)


def _abre_artigo_novo(atual: _ArtigoEmMontagem | None, numero: int | None) -> bool:
    """Falso quando o `Art. N` é artigo de outra norma citado dentro deste.

    Normas alteradoras reproduzem o texto que alteram ("...passa a vigorar com a
    seguinte redação: 'Art. 3º ...'"). A numeração de um normativo é estritamente
    crescente, então um número que não avança é citação, não artigo novo — abrir
    um chunk pai para ele criaria duas fontes com o mesmo rótulo de citação.
    """
    if atual is None or numero is None:
        return True
    return numero > atual.numero


def montar_chunks(blocos: list[Bloco], norma: Norma) -> tuple[list[ChunkPai], list[ChunkFilho]]:
    """Devolve (chunks pai, chunks filho) na ordem do documento.

    Blocos riscados são descartados: são redação substituída que o BCB mantém no
    HTML. Blocos anteriores ao primeiro artigo (ementa, preâmbulo) não viram
    chunk — só atualizam o capítulo e a seção correntes.
    """
    pais: list[ChunkPai] = []
    filhos: list[ChunkFilho] = []
    capitulo: str | None = None
    secao: str | None = None
    atual: _ArtigoEmMontagem | None = None
    paragrafo_corrente: str | None = None
    inciso_corrente: str | None = None
    aguardando_titulo: str | None = None
    descartados = 0

    def fechar(artigo: _ArtigoEmMontagem | None) -> None:
        if artigo is None:
            return
        id_pai = f"{norma.id_norma}::art-{artigo.numero}"
        pais.append(
            ChunkPai(
                id=id_pai,
                id_norma=norma.id_norma,
                norma=norma.rotulo,
                artigo=artigo.artigo,
                numero_artigo=artigo.numero,
                texto="\n".join(artigo.linhas),
                capitulo=artigo.capitulo,
                secao=artigo.secao,
                revogada=norma.revogada,
            )
        )
        for ordem, (rotulo, texto) in enumerate(artigo.unidades):
            filhos.append(
                ChunkFilho(
                    id=f"{id_pai}::u{ordem}",
                    id_pai=id_pai,
                    id_norma=norma.id_norma,
                    norma=norma.rotulo,
                    artigo=artigo.artigo,
                    numero_artigo=artigo.numero,
                    rotulo=rotulo,
                    texto=texto,
                    texto_indexavel=_metadata_do_indice(norma, rotulo, artigo.caput, texto),
                    capitulo=artigo.capitulo,
                    secao=artigo.secao,
                    revogada=norma.revogada,
                )
            )

    for bloco in blocos:
        if bloco.riscado:
            descartados += 1
            continue
        if bloco.tipo == "capitulo":
            capitulo, secao, aguardando_titulo = bloco.texto, None, "capitulo"
            continue
        if bloco.tipo == "secao":
            secao, aguardando_titulo = bloco.texto, "secao"
            continue

        # O BCB publica "CAPÍTULO III" e o título dele em linhas separadas. Sem
        # juntar as duas, o título viraria uma unidade do artigo anterior — e a
        # metadata perderia justamente a palavra que descreve o capítulo.
        if aguardando_titulo and bloco.tipo == "outro":
            if aguardando_titulo == "capitulo" and capitulo:
                capitulo = f"{capitulo} — {bloco.texto}"
            elif secao:
                secao = f"{secao} — {bloco.texto}"
            aguardando_titulo = None
            continue
        aguardando_titulo = None

        if bloco.tipo == "artigo" and _abre_artigo_novo(atual, bloco.numero_artigo):
            fechar(atual)
            assert bloco.numero_artigo is not None
            atual = _ArtigoEmMontagem(
                numero=bloco.numero_artigo,
                artigo=bloco.rotulo,
                capitulo=capitulo,
                secao=secao,
                caput=bloco.texto,
            )
            paragrafo_corrente = None
            inciso_corrente = None
            atual.linhas.append(bloco.texto)
            atual.unidades.append((bloco.rotulo, bloco.texto))
            continue

        if atual is None:
            continue  # ementa, preâmbulo e considerandos não são citáveis por artigo

        if bloco.tipo == "paragrafo":
            paragrafo_corrente, inciso_corrente = bloco.rotulo, None
            rotulo = f"{atual.artigo}, {bloco.rotulo}"
        elif bloco.tipo == "inciso":
            inciso_corrente = bloco.rotulo
            partes = [atual.artigo, paragrafo_corrente, bloco.rotulo]
            rotulo = ", ".join(p for p in partes if p)
        elif bloco.tipo == "alinea":
            partes = [atual.artigo, paragrafo_corrente, inciso_corrente, bloco.rotulo]
            rotulo = ", ".join(p for p in partes if p)
        else:
            partes = [atual.artigo, paragrafo_corrente, inciso_corrente]
            rotulo = ", ".join(p for p in partes if p)

        atual.linhas.append(bloco.texto)
        atual.unidades.append((rotulo, bloco.texto))

    fechar(atual)
    logger.info(
        "chunking concluido",
        extra={
            "norma": norma.id_norma,
            "pais": len(pais),
            "filhos": len(filhos),
            "blocos_riscados_descartados": descartados,
        },
    )
    return pais, filhos
