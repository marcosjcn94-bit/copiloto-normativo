"""Orquestra o pipeline: denso + esparso -> RRF -> rerank -> artigo inteiro.

Este é o único módulo da camada que lê `config/parametros.toml`. Os outros
recebem `k` e threshold como argumento — assim nenhum deles precisa saber que
existe um arquivo de configuração, e o teste de cada um roda com valor
literal sem tocar em disco.

A saída não é o chunk que foi indexado: é o **artigo inteiro**, buscado no
catálogo SQL pelo `id_pai`. "Vetorizar o parágrafo, entregar o artigo" (§4.2.6
do briefing) — o filho é pequeno, então a busca é precisa; o pai é coerente,
então a citação é verificável.
"""

from __future__ import annotations

import logging
import sqlite3
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from copiloto.recuperacao.adapters.base import AdaptadorVetorial, Ocorrencia
from copiloto.recuperacao.denso import BuscaDensa
from copiloto.recuperacao.esparso import BuscaEsparsa
from copiloto.recuperacao.fusao import rrf
from copiloto.recuperacao.rerank import Reordenador

logger = logging.getLogger(__name__)

Modo = Literal["denso", "hibrido", "hibrido_rerank"]
MODOS: tuple[Modo, ...] = ("denso", "hibrido", "hibrido_rerank")


@dataclass(frozen=True, slots=True)
class ParametrosRecuperacao:
    """A seção `[recuperacao]` do TOML, tipada.

    Existe para que um erro de digitação na chave apareça no carregamento, e não
    três camadas abaixo como `KeyError` no meio de uma busca.
    """

    k_denso: int
    k_esparso: int
    k_rrf: int
    k_final: int
    score_minimo: float
    colecao: str
    modelo_embedding: str
    modelo_rerank: str


@dataclass(frozen=True, slots=True)
class Trecho:
    """O que chega ao LLM: um artigo, citável, com a vigência à vista."""

    id: str
    id_norma: str
    norma: str
    artigo: str
    numero_artigo: int
    texto: str
    score: float
    revogada: bool
    capitulo: str = ""
    secao: str = ""

    @property
    def citacao(self) -> str:
        """`Resolução BCB nº 85, de 2021, art. 7º` — mais o aviso, se revogada."""
        base = f"{self.norma}, {self.artigo.lower()}"
        return f"{base} (REVOGADA)" if self.revogada else base


def carregar_parametros(caminho: Path) -> ParametrosRecuperacao:
    """Lê `config/parametros.toml`. Chave a mais ou a menos falha aqui."""
    secao = tomllib.loads(caminho.read_text(encoding="utf-8"))["recuperacao"]
    return ParametrosRecuperacao(**secao)


class Recuperador:
    """O pipeline inteiro por trás de um método."""

    def __init__(
        self,
        *,
        densa: BuscaDensa,
        esparsa: BuscaEsparsa,
        reordenador: Reordenador,
        parametros: ParametrosRecuperacao,
        catalogo: sqlite3.Connection | None = None,
    ) -> None:
        self._densa = densa
        self._esparsa = esparsa
        self._reordenador = reordenador
        self._parametros = parametros
        self._catalogo = catalogo

    @classmethod
    def abrir(cls, raiz: Path, *, adaptador: AdaptadorVetorial | None = None) -> Recuperador:
        """Monta o pipeline a partir dos artefatos que a Fase 2 deixou em disco.

        `adaptador` é injetável para que trocar Chroma por Azure AI Search não
        precise de nenhuma alteração aqui — é a razão de o `Protocol` existir.
        """
        parametros = carregar_parametros(raiz / "config" / "parametros.toml")
        if adaptador is None:
            from copiloto.recuperacao.adapters.chroma import AdaptadorChroma

            adaptador = AdaptadorChroma.abrir(
                raiz / "indices" / "chroma", colecao=parametros.colecao
            )
        catalogo = sqlite3.connect(raiz / "data" / "catalogo.sqlite")
        catalogo.row_factory = sqlite3.Row
        return cls(
            densa=BuscaDensa(adaptador, modelo=parametros.modelo_embedding),
            esparsa=BuscaEsparsa.carregar(raiz / "indices" / "bm25.json"),
            reordenador=Reordenador(
                modelo=parametros.modelo_rerank,
                cache=str(raiz / "indices" / "flashrank"),
            ),
            parametros=parametros,
            catalogo=catalogo,
        )

    @property
    def parametros(self) -> ParametrosRecuperacao:
        return self._parametros

    def buscar(
        self,
        pergunta: str,
        *,
        modo: Modo = "hibrido_rerank",
        filtro: Mapping[str, Any] | None = None,
        k_final: int | None = None,
    ) -> list[Trecho]:
        """Executa o pipeline e devolve até `k_final` artigos.

        `modo` existe para a tabela de ablação da Fase 3: as três configurações
        medidas são três chamadas deste mesmo método, não três implementações
        paralelas que poderiam divergir e falsear a comparação.
        """
        if modo not in MODOS:
            raise ValueError(f"modo desconhecido: {modo!r}")
        p = self._parametros
        limite = k_final if k_final is not None else p.k_final

        densas = self._densa.buscar(pergunta, k=p.k_denso, filtro=filtro)
        conhecidas = {o.id: o for o in densas}

        if modo == "denso":
            candidatas = densas
        else:
            esparsas = self._esparsa.buscar(pergunta, k=p.k_esparso)
            fundidos = rrf([[o.id for o in densas], [id_ for id_, _ in esparsas]], k_rrf=p.k_rrf)
            candidatas = self._hidratar(fundidos, conhecidas)

        if modo == "hibrido_rerank":
            candidatas = self._reordenador.reordenar(pergunta, candidatas)
            candidatas = [o for o in candidatas if o.score >= p.score_minimo]

        return self._entregar_artigos(candidatas, limite=limite)

    def _hidratar(
        self, fundidos: Sequence[tuple[str, float]], conhecidas: Mapping[str, Ocorrencia]
    ) -> list[Ocorrencia]:
        """Dá texto aos ids que só o BM25 viu, em uma ida ao adapter, não `n`."""
        faltantes = [id_ for id_, _ in fundidos if id_ not in conhecidas]
        buscadas = self._densa.obter(faltantes) if faltantes else {}
        saida: list[Ocorrencia] = []
        for id_, score in fundidos:
            achada = conhecidas.get(id_) or buscadas.get(id_)
            if achada is None:
                # Id no BM25 sem par no vetorial: os dois índices saíram de
                # execuções diferentes da indexação. Não é fatal para a busca,
                # mas é sinal de que `indexacao.py` precisa rodar de novo.
                logger.warning("id do esparso ausente no vetorial", extra={"id": id_})
                continue
            saida.append(
                Ocorrencia(id=achada.id, texto=achada.texto, metadata=achada.metadata, score=score)
            )
        return saida

    def _entregar_artigos(self, candidatas: Sequence[Ocorrencia], *, limite: int) -> list[Trecho]:
        """Colapsa chunks filhos no artigo pai, na ordem em que chegaram.

        Vários parágrafos do mesmo artigo costumam ser recuperados juntos.
        Entregá-los como trechos distintos gastaria a janela de contexto três
        vezes com o mesmo artigo — a deduplicação por pai é o que faz `k_final`
        significar "cinco artigos" e não "cinco parágrafos".
        """
        melhores: dict[str, Ocorrencia] = {}
        for ocorrencia in candidatas:
            id_pai = ocorrencia.id_pai
            if not id_pai:
                # Chunk sem pai é chunk indexado por uma versão anterior do
                # `chunking.py`. Ignorar é melhor que citar sem artigo.
                logger.warning("chunk sem id_pai na metadata", extra={"id": ocorrencia.id})
                continue
            if id_pai not in melhores and len(melhores) >= limite:
                break
            if id_pai not in melhores:
                melhores[id_pai] = ocorrencia

        # `candidatas` já vem ordenada do melhor para o pior, e o primeiro filho
        # de um artigo é o melhor colocado dele — a ordem de inserção do dict é
        # a ordem final, sem reordenar nada.
        textos = self._textos_dos_artigos(list(melhores))
        return [
            _para_trecho(id_pai, ocorrencia, textos.get(id_pai))
            for id_pai, ocorrencia in melhores.items()
        ]

    def _textos_dos_artigos(self, ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        """Busca o artigo inteiro no catálogo. Sem catálogo, o filho é o que há."""
        if self._catalogo is None or not ids:
            return {}
        # Interpolação só dos marcadores `?`, cuja quantidade vem de `len(ids)`.
        # Nenhum valor entra na string: os ids vão ligados como parâmetros.
        marcadores = ",".join("?" * len(ids))
        linhas = self._catalogo.execute(
            "SELECT id, texto, artigo, numero_artigo, capitulo, secao FROM artigos "
            f"WHERE id IN ({marcadores})",
            list(ids),
        ).fetchall()
        return {linha["id"]: linha for linha in linhas}


def _para_trecho(id_pai: str, ocorrencia: Ocorrencia, artigo: sqlite3.Row | None) -> Trecho:
    """Metadata do chunk + texto do artigo. Se o catálogo não tem o pai, cai no filho."""
    meta = ocorrencia.metadata
    return Trecho(
        id=id_pai,
        id_norma=str(meta.get("id_norma", "")),
        norma=str(meta.get("norma", "")),
        artigo=str(artigo["artigo"] if artigo is not None else meta.get("artigo", "")),
        numero_artigo=int(
            artigo["numero_artigo"] if artigo is not None else meta.get("numero_artigo", 0)
        ),
        texto=str(artigo["texto"]) if artigo is not None else ocorrencia.texto,
        score=ocorrencia.score,
        revogada=bool(meta.get("revogada", False)),
        capitulo=str(artigo["capitulo"] if artigo is not None else meta.get("capitulo", "")),
        secao=str(artigo["secao"] if artigo is not None else meta.get("secao", "")),
    )
