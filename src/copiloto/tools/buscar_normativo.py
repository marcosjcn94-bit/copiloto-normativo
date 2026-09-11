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

**Sobre o escopo por norma.** É outra coisa, e por isso é feito de outro jeito:
não é corte pós-recuperação, é redução do universo *antes* da busca, nas duas
pontas (`where` no vetorial, `ids_permitidos` no BM25). A diferença importa —
filtrar depois só devolveria os artigos daquela norma que por acaso entrassem no
top-30 do corpus inteiro, que é exatamente o que já não estava acontecendo.

O escopo sai do parâmetro `numero`, se o modelo o informar, e da própria pergunta
se não. A detecção automática existe porque ela é o que torna o ganho *medível*:
`evals/ablacao.py` roda sobre perguntas, não sobre chamadas de tool, e uma
melhoria que só aparece quando o LLM lembra de preencher um campo não pode ser
distinguida de uma melhoria na recuperação. Com a detecção aqui, a tabela de
ablação mede o mesmo caminho que a produção executa.

A detecção foi conferida antes de ser adotada: das 32 perguntas `rag` do
gabarito, 14 nomeiam uma norma, e nas 14 a norma nomeada é a que contém a
resposta esperada — nenhuma divergência e nenhum número ambíguo. `tests/
test_tools.py` fixa isso; se uma pergunta futura citar uma norma de passagem e
esperar resposta em outra, é lá que aparece.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from copiloto.recuperacao.retriever import ParametrosRecuperacao, Trecho
from copiloto.tools.schemas import (
    PADRAO_TIPOS_NORMA,
    EntradaBuscarNormativo,
    SaidaBuscarNormativo,
    TrechoCitado,
)

logger = logging.getLogger(__name__)

# Referência a uma norma SEM artigo — irmã do `_CITACAO` de `grafo.guardrails`,
# que exige o artigo porque valida citação. Aqui o artigo é justamente o que não
# se sabe: a pergunta diz em que norma procurar, e achar o artigo é o trabalho.
# Os dois regexes compartilham `PADRAO_TIPOS_NORMA` para que o vocabulário de
# tipos não possa divergir entre quem busca e quem confere.
_NORMA_CITADA = re.compile(
    rf"(?P<tipo>{PADRAO_TIPOS_NORMA})"
    r"\s*(?:n[º°o]\.?\s*)?"
    # Mesma dupla grafia do número que custou caro na Fase 7: `4.893` e `3979`.
    # A alternativa com separador vem primeiro, senão `\d{1,6}` casa `4` e para.
    r"(?P<numero>\d{1,3}(?:\.\d{3})+|\d{1,6})"
    r"(?:\s*(?:,\s*de\s*|/)\s*(?P<ano>\d{4}))?",
    re.IGNORECASE,
)


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

    def buscar(
        self,
        pergunta: str,
        *,
        k_final: int | None = ...,
        escopo: str | None = ...,
    ) -> list[Trecho]: ...


NOME = "buscar_normativo"

DESCRICAO = (
    "Busca trechos de normas do Banco Central por significado e devolve o artigo "
    "inteiro, com a norma, o número do artigo e o status de vigência. "
    "Use para perguntas sobre o CONTEÚDO das normas ('o que a norma exige sobre X'). "
    "Se a pergunta disser em qual norma procurar, passe o número dela em `numero`: "
    "buscar dentro de uma norma é bem mais preciso que buscar no corpus inteiro. "
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

    escopo = escopo_para(entrada, catalogo)
    if escopo is NORMA_INEXISTENTE:
        # O modelo pediu uma norma que o corpus não tem. Zero trechos é a
        # resposta certa e o agente a trata como qualquer busca vazia; buscar no
        # corpus inteiro «para não voltar de mãos vazias» devolveria artigo de
        # outra norma para uma pergunta que nomeou a sua.
        return SaidaBuscarNormativo(trechos=(), total=0, ha_revogada=False)

    trechos = recuperador.buscar(entrada.pergunta, k_final=limite, escopo=escopo)

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


# Sentinela: «o pedido nomeou uma norma e ela não está no corpus». Diferente de
# `None`, que é «nenhuma norma foi nomeada, busque em tudo». Duas ausências com
# consequências opostas não podem compartilhar o mesmo valor.
NORMA_INEXISTENTE = "\x00norma-inexistente"


@dataclass(frozen=True, slots=True)
class ReferenciaDeNorma:
    """Uma norma nomeada em texto, já normalizada para casar com o catálogo."""

    tipo: str
    numero: str
    ano: str


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _normalizar_tipo(tipo: str) -> str:
    return re.sub(r"\s+", " ", _sem_acento(tipo).strip().lower())


def normas_citadas(texto: str) -> tuple[ReferenciaDeNorma, ...]:
    """Toda norma nomeada no texto, com ou sem ano, na ordem em que aparece."""
    return tuple(
        ReferenciaDeNorma(
            tipo=_normalizar_tipo(achado["tipo"]),
            numero=achado["numero"].replace(".", ""),
            ano=achado["ano"] or "",
        )
        for achado in _NORMA_CITADA.finditer(texto)
    )


def resolver_norma(
    catalogo: sqlite3.Connection | None, referencia: ReferenciaDeNorma
) -> str | None:
    """O `id_norma` do catálogo que corresponde à referência, se houver um só.

    Devolve `None` quando não acha e também quando acha mais de uma: número
    ambíguo (`Resolução BCB nº 85` e `Resolução CMN nº 85` coexistem no domínio)
    escopado no palpite errado é pior que não escopado — a busca ampla ainda pode
    achar o artigo certo, a busca escopada na norma errada não pode.
    """
    if catalogo is None:
        return None
    linhas = catalogo.execute(
        "SELECT id_norma, tipo, ano FROM normas WHERE replace(numero, '.', '') = ?",
        (referencia.numero,),
    ).fetchall()
    candidatas = [
        linha
        for linha in linhas
        if _casa_tipo(referencia.tipo, _normalizar_tipo(str(linha[1])))
        and (not referencia.ano or str(linha[2]) == referencia.ano)
    ]
    if len(candidatas) != 1:
        if len(candidatas) > 1:
            logger.info(
                "referencia de norma ambigua, busca segue ampla",
                extra={"numero": referencia.numero, "candidatas": len(candidatas)},
            )
        return None
    return str(candidatas[0][0])


def _casa_tipo(pedido: str, catalogado: str) -> bool:
    """Casa por continência: `resolucao` casa `resolucao bcb`, `bcb` não casa `cmn`.

    Mesma regra do `Citacao.combina` da Fase 5, pelo mesmo motivo: o texto diz
    «Resolução nº 4.893» e o catálogo diz «Resolução CMN». Tratar isso como
    divergência jogaria fora o escopo justamente nas perguntas mais fáceis.
    """
    return pedido in catalogado or catalogado in pedido


def escopo_para(entrada: EntradaBuscarNormativo, catalogo: sqlite3.Connection | None) -> str | None:
    """A norma a que restringir a busca: a pedida, ou a que a pergunta nomeia.

    O parâmetro explícito e a detecção automática não falham do mesmo jeito, e a
    diferença é deliberada. Número explícito é uma afirmação do modelo sobre o
    que ele quer: se não existe no corpus, a busca não tem o que devolver.
    Detecção automática é palpite nosso sobre o texto do usuário: se não resolve,
    o palpite é descartado e a busca segue ampla, como sempre foi.

    Sem catálogo aberto há um terceiro caso, e ele não é nenhum dos dois: não se
    sabe se a norma existe. «Não consigo verificar» não pode virar «não existe»,
    então a busca segue ampla e o aviso vai para o log — dizer que uma norma não
    está no corpus é afirmação sobre o corpus, e ela precisa do corpus.
    """
    if entrada.numero is not None and catalogo is None:
        logger.warning(
            "numero de norma pedido sem catalogo aberto: busca segue ampla",
            extra={"numero": entrada.numero},
        )
        return None

    if entrada.numero is not None:
        referencia = ReferenciaDeNorma(
            tipo=_normalizar_tipo(entrada.tipo or ""),
            numero=entrada.numero.replace(".", ""),
            ano="",
        )
        achado = resolver_norma(catalogo, referencia)
        if achado is None:
            logger.info(
                "norma pedida nao existe no corpus",
                extra={"numero": entrada.numero, "tipo": entrada.tipo},
            )
            return NORMA_INEXISTENTE
        return achado

    for referencia in normas_citadas(entrada.pergunta):
        achado = resolver_norma(catalogo, referencia)
        if achado is not None:
            logger.info("escopo deduzido da pergunta", extra={"id_norma": achado})
            return achado
    return None


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
