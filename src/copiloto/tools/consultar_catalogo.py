"""Tool 2: consulta de metadado no catálogo SQL (§7 do briefing).

**A decisão a defender.** "Quais normas sobre nuvem estão vigentes?" não é
pergunta de similaridade. A resposta precisa ser *completa* e *exata*, e busca
vetorial não garante nenhuma das duas: ela devolve os k mais parecidos, não
todos os que satisfazem o critério. Isso é `WHERE tema = ? AND status_vigencia
= ?`. Saber onde **não** usar RAG é o sinal mais forte de domínio de RAG.

A montagem do SQL é por fragmentos fixos com parâmetros ligados. Nenhum valor de
entrada entra na string da consulta — nem depois de validado pelo Pydantic.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from copiloto.tools.schemas import (
    EntradaConsultarCatalogo,
    NormaCatalogada,
    SaidaConsultarCatalogo,
)

logger = logging.getLogger(__name__)

NOME = "consultar_catalogo"

DESCRICAO = (
    "Lista normas do Banco Central por METADADO — tema, tipo, número, ano e status de "
    "vigência — consultando o catálogo SQL. A resposta é completa e exata dentro do "
    "corpus indexado. Use para 'quais normas sobre X estão vigentes', 'a Resolução nº Y "
    "ainda vale', 'normas de nuvem posteriores a 2020'. "
    "NÃO faz: não devolve o texto dos artigos nem busca por significado (para isso use "
    "buscar_normativo), não interpreta a norma e não escreve em lugar nenhum."
)

COLUNAS = "id_norma, tipo, numero, ano, data, tema, status_vigencia, url, titulo, ementa, artigos"


def consultar_catalogo(
    entrada: EntradaConsultarCatalogo,
    *,
    catalogo: sqlite3.Connection,
    limite_padrao: int,
) -> SaidaConsultarCatalogo:
    """Filtra a tabela `normas` e devolve as linhas, ordenadas da mais nova."""
    condicoes, valores = _condicoes(entrada)
    limite = entrada.limite if entrada.limite is not None else limite_padrao

    onde = f" WHERE {' AND '.join(condicoes)}" if condicoes else ""
    # `limite + 1` para saber se cortou sem precisar de um segundo COUNT.
    linhas = catalogo.execute(
        f"SELECT {COLUNAS} FROM normas{onde} ORDER BY ano DESC, data DESC, numero DESC LIMIT ?",
        (*valores, limite + 1),
    ).fetchall()

    truncado = len(linhas) > limite
    entregues = linhas[:limite]
    logger.info(
        "consultar_catalogo",
        extra={"condicoes": len(condicoes), "linhas": len(entregues), "truncado": truncado},
    )
    return SaidaConsultarCatalogo(
        normas=tuple(_para_norma(linha) for linha in entregues),
        total=len(entregues),
        truncado=truncado,
    )


def _condicoes(entrada: EntradaConsultarCatalogo) -> tuple[list[str], list[Any]]:
    """Traduz os filtros em fragmentos com `?` — nunca em texto interpolado."""
    condicoes: list[str] = []
    valores: list[Any] = []

    if entrada.tema is not None:
        condicoes.append("tema = ?")
        valores.append(entrada.tema)
    if entrada.tipo is not None:
        condicoes.append("tipo = ?")
        valores.append(entrada.tipo)
    if entrada.numero is not None:
        # O corpus grava tanto "4.893" quanto "85"; o ponto de milhar é
        # cosmético e não pode decidir se a norma é achada ou não.
        condicoes.append("REPLACE(numero, '.', '') = ?")
        valores.append(entrada.numero.replace(".", ""))
    if entrada.status_vigencia != "todas":
        condicoes.append("status_vigencia = ?")
        valores.append(entrada.status_vigencia)
    if entrada.ano_minimo is not None:
        condicoes.append("ano >= ?")
        valores.append(entrada.ano_minimo)
    if entrada.ano_maximo is not None:
        condicoes.append("ano <= ?")
        valores.append(entrada.ano_maximo)

    return condicoes, valores


def _para_norma(linha: sqlite3.Row) -> NormaCatalogada:
    return NormaCatalogada(
        id_norma=linha["id_norma"],
        tipo=linha["tipo"],
        numero=linha["numero"],
        ano=linha["ano"],
        data=linha["data"],
        tema=linha["tema"],
        status_vigencia=linha["status_vigencia"],
        url=linha["url"],
        titulo=linha["titulo"] or "",
        ementa=linha["ementa"] or "",
        artigos=linha["artigos"] or 0,
    )
