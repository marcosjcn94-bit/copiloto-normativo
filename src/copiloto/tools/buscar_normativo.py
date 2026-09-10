"""Tool 1: busca semântica por trecho de norma (§7 do briefing).

Envolve o pipeline híbrido da Fase 3 e devolve artigos citáveis. Não decide
nada: recebe entrada já validada, chama o recuperador e tipa a saída.

**Sobre o filtro de tema e vigência.** Ele é aplicado depois da recuperação,
sobre os trechos devolvidos, e não como `where` do índice vetorial. Dois motivos:
`tema` não está na metadata do chunk (é atributo da norma, e vive no catálogo
SQL), e o BM25 não aceita filtro — empurrar o corte só para o lado denso tornaria
o resultado do modo híbrido diferente do resultado dos modos medidos na ablação,
que é justamente o que a tabela da Fase 3 existe para impedir. O preço é a
sobrebusca (`fator_sobrebusca` em `config/parametros.toml`), e ele é explícito.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from copiloto.recuperacao.retriever import ParametrosRecuperacao, Trecho
from copiloto.tools.schemas import (
    EntradaBuscarNormativo,
    SaidaBuscarNormativo,
    TrechoCitado,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class FonteDeTrechos(Protocol):
    """O que a tool precisa do recuperador — nada além disso.

    Depender do `Recuperador` concreto amarraria a tool à implementação da Fase 3
    e obrigaria todo teste a montar Chroma, BM25 e FlashRank para exercitar duas
    linhas de filtro. O `Recuperador` satisfaz este protocolo sem saber que ele
    existe, que é o ponto de tipagem estrutural.
    """

    @property
    def parametros(self) -> ParametrosRecuperacao: ...

    def buscar(self, pergunta: str, *, k_final: int | None = ...) -> list[Trecho]: ...


NOME = "buscar_normativo"

DESCRICAO = (
    "Busca trechos de normas do Banco Central por significado e devolve o artigo "
    "inteiro, com a norma, o número do artigo e o status de vigência. "
    "Use para perguntas sobre o CONTEÚDO das normas ('o que a norma exige sobre X'). "
    "NÃO faz: não interpreta a norma, não emite parecer jurídico, não afirma "
    "conformidade, não lista normas por metadado (para 'quais normas sobre nuvem estão "
    "vigentes' use consultar_catalogo) e não escreve em lugar nenhum."
)


def buscar_normativo(
    entrada: EntradaBuscarNormativo,
    *,
    recuperador: FonteDeTrechos,
    catalogo: sqlite3.Connection | None = None,
    fator_sobrebusca: int = 1,
) -> SaidaBuscarNormativo:
    """Executa o pipeline híbrido e devolve até `k` artigos citáveis."""
    k = entrada.k if entrada.k is not None else recuperador.parametros.k_final
    filtra = entrada.tema is not None or entrada.apenas_vigentes
    limite = k * fator_sobrebusca if filtra else k

    trechos = recuperador.buscar(entrada.pergunta, k_final=limite)

    if entrada.tema is not None:
        do_tema = _normas_do_tema(catalogo, entrada.tema)
        trechos = [t for t in trechos if t.id_norma in do_tema]
    if entrada.apenas_vigentes:
        trechos = [t for t in trechos if not t.revogada]

    entregues = trechos[:k]
    logger.info(
        "buscar_normativo",
        extra={"tema": entrada.tema, "pedidos": k, "entregues": len(entregues)},
    )
    return SaidaBuscarNormativo(
        trechos=tuple(_citar(t) for t in entregues),
        total=len(entregues),
        ha_revogada=any(t.revogada for t in entregues),
    )


def _normas_do_tema(catalogo: sqlite3.Connection | None, tema: str) -> frozenset[str]:
    """Quais normas pertencem ao tema. Metadado é SQL, também aqui."""
    if catalogo is None:
        logger.warning("filtro de tema pedido sem catálogo aberto", extra={"tema": tema})
        return frozenset()
    linhas: Iterable[sqlite3.Row] = catalogo.execute(
        "SELECT id_norma FROM normas WHERE tema = ?", (tema,)
    ).fetchall()
    return frozenset(linha[0] for linha in linhas)


def _citar(trecho: Trecho) -> TrechoCitado:
    """`Trecho` da camada de recuperação vira a saída tipada da tool.

    A conversão existe para que o contrato da tool não fique amarrado ao
    `dataclass` interno do retriever: mudar um campo lá não pode mudar em
    silêncio o que o modelo recebe.
    """
    return TrechoCitado(
        id=trecho.id,
        id_norma=trecho.id_norma,
        norma=trecho.norma,
        artigo=trecho.artigo,
        citacao=trecho.citacao,
        texto=trecho.texto,
        score=trecho.score,
        revogada=trecho.revogada,
        capitulo=trecho.capitulo,
        secao=trecho.secao,
    )
