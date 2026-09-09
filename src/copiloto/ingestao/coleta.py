"""Coleta dos normativos declarados em `config/corpus.toml`.

Idempotente por desenho: o que já está em disco não é rebaixado. Rodar de novo
não rebaixa nada — sem isso, uma coleta interrompida no meio deixaria o corpus
pela metade e a segunda execução gastaria a fonte de novo.

A rede entra por um cliente injetado (`Callable[[str, dict], dict]`), o que
mantém o módulo testável sem bater no BCB.
"""

from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from copiloto.ingestao.chunking import Norma
from copiloto.resiliencia import Disjuntor, DisjuntorAberto, PoliticaDeRetentativa, com_retentativa

logger = logging.getLogger(__name__)

STATUS_VALIDOS = frozenset({"vigente", "revogada"})


@dataclass(frozen=True, slots=True)
class NormaDoCorpus:
    """Uma linha de `[[norma]]` do `corpus.toml`, já validada."""

    tipo: str
    numero: str
    ano: int
    data: date
    tema: str
    status_vigencia: str
    url: str

    @property
    def norma(self) -> Norma:
        return Norma(
            tipo=self.tipo,
            numero=self.numero,
            ano=self.ano,
            revogada=self.status_vigencia == "revogada",
        )

    @property
    def id_norma(self) -> str:
        return self.norma.id_norma


@dataclass(frozen=True, slots=True)
class Corpus:
    endpoint_normativo: str
    endpoint_busca: str
    normas: list[NormaDoCorpus]


@dataclass
class RelatorioDeColeta:
    """O que a execução fez — é isto que prova a idempotência."""

    baixadas: list[str] = field(default_factory=list)
    reaproveitadas: list[str] = field(default_factory=list)
    falhas: dict[str, str] = field(default_factory=dict)
    divergencias_de_vigencia: dict[str, str] = field(default_factory=dict)

    @property
    def total_em_disco(self) -> int:
        return len(self.baixadas) + len(self.reaproveitadas)


def carregar_corpus(caminho: Path) -> Corpus:
    """Lê e valida o `corpus.toml`. Erro aqui é erro de curadoria, não de rede."""
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    fonte = dados["fonte"]
    normas: list[NormaDoCorpus] = []
    vistos: set[str] = set()
    for bruto in dados["norma"]:
        norma = NormaDoCorpus(**bruto)
        if norma.status_vigencia not in STATUS_VALIDOS:
            raise ValueError(
                f"status_vigencia inválido em {norma.id_norma}: {norma.status_vigencia}"
            )
        if norma.id_norma in vistos:
            raise ValueError(f"norma duplicada no corpus: {norma.id_norma}")
        vistos.add(norma.id_norma)
        normas.append(norma)
    return Corpus(
        endpoint_normativo=fonte["endpoint_normativo"],
        endpoint_busca=fonte["endpoint_busca"],
        normas=normas,
    )


def caminho_do_bruto(destino: Path, norma: NormaDoCorpus) -> Path:
    return destino / f"{norma.id_norma}.json"


def _escolher_versao(conteudo: list[dict[str, Any]], norma: NormaDoCorpus) -> dict[str, Any]:
    """A fonte pode devolver mais de um registro para o mesmo número.

    Desempata pelo ano declarado no corpus — número de norma se repete entre
    tipos e épocas, e pegar o primeiro seria sorte, não critério.
    """
    for registro in conteudo:
        if str(registro.get("Data", ""))[:4] == str(norma.ano):
            return registro
    return conteudo[0]


def coletar(
    corpus: Corpus,
    destino: Path,
    *,
    buscar: Callable[[str, dict[str, str]], list[dict[str, Any]]],
    forcar: bool = False,
    politica: PoliticaDeRetentativa | None = None,
    disjuntor: Disjuntor | None = None,
    dormir: Callable[[float], None] | None = None,
) -> RelatorioDeColeta:
    """Baixa o que falta e devolve o relatório do que fez.

    `forcar=True` rebaixa tudo — é a saída para quando a fonte publica nova
    versão de uma norma já coletada.
    """
    destino.mkdir(parents=True, exist_ok=True)
    protecao = disjuntor or Disjuntor()
    relatorio = RelatorioDeColeta()
    extras: dict[str, Any] = {"dormir": dormir} if dormir else {}

    for norma in corpus.normas:
        arquivo = caminho_do_bruto(destino, norma)
        if arquivo.exists() and not forcar:
            relatorio.reaproveitadas.append(norma.id_norma)
            continue
        try:
            conteudo = com_retentativa(
                lambda n=norma: protecao.executar(
                    lambda: buscar(corpus.endpoint_normativo, {"p1": n.tipo, "p2": n.numero})
                ),
                politica=politica,
                descricao=f"coleta {norma.id_norma}",
                **extras,
            )
        except DisjuntorAberto as erro:
            relatorio.falhas[norma.id_norma] = f"disjuntor aberto: {erro}"
            logger.error("coleta interrompida", extra={"norma": norma.id_norma})
            continue
        except Exception as erro:  # noqa: BLE001 - a falha de uma norma não derruba o corpus
            relatorio.falhas[norma.id_norma] = f"{type(erro).__name__}: {erro}"
            continue

        if not conteudo:
            relatorio.falhas[norma.id_norma] = "fonte devolveu conteúdo vazio"
            continue

        registro = _escolher_versao(conteudo, norma)
        coletado = "revogada" if registro.get("Revogado") else "vigente"
        if coletado != norma.status_vigencia:
            relatorio.divergencias_de_vigencia[norma.id_norma] = (
                f"declarado={norma.status_vigencia} coletado={coletado}"
            )
        arquivo.write_text(json.dumps(registro, ensure_ascii=False), encoding="utf-8")
        relatorio.baixadas.append(norma.id_norma)

    logger.info(
        "coleta concluida",
        extra={
            "baixadas": len(relatorio.baixadas),
            "reaproveitadas": len(relatorio.reaproveitadas),
            "falhas": len(relatorio.falhas),
            "divergencias": len(relatorio.divergencias_de_vigencia),
        },
    )
    return relatorio


def buscar_no_bcb(timeout: float = 60.0) -> Callable[[str, dict[str, str]], list[dict[str, Any]]]:
    """Cliente real da Busca de Normas. Isolado para que o resto seja testável."""
    import httpx

    def buscar(endpoint: str, parametros: dict[str, str]) -> list[dict[str, Any]]:
        resposta = httpx.get(endpoint, params=parametros, timeout=timeout)
        resposta.raise_for_status()
        return resposta.json().get("conteudo") or []

    return buscar
