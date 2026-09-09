"""Retentativa com backoff, disjuntor e chave de idempotência.

Três mecanismos que a coleta da Fase 2 usa e que as tools da Fase 4 reusam:

* **Backoff exponencial com jitter.** Repetir na mesma cadência sincroniza todos
  os clientes que falharam juntos e derruba o servidor de novo; o jitter é o que
  quebra essa sincronia.
* **Disjuntor.** Depois de N falhas seguidas, para de tentar por uma janela. Sem
  ele, uma fonte fora do ar vira 22 sequências de retentativa em série.
* **Chave de idempotência.** Derivada do conteúdo, não do relógio: a mesma
  operação repetida produz a mesma chave, e é isso que impede o reenvio de virar
  registro duplicado no CRM (§7 do briefing).

O tempo entra por parâmetro (`dormir`, `agora`) para que o comportamento seja
testável sem `sleep` de verdade.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PoliticaDeRetentativa:
    """Quantas vezes tentar e quanto esperar entre as tentativas."""

    tentativas: int = 4
    espera_inicial: float = 1.0
    fator: float = 2.0
    espera_maxima: float = 30.0
    jitter: float = 0.5

    def __post_init__(self) -> None:
        if self.tentativas < 1:
            raise ValueError("tentativas precisa ser >= 1")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("jitter precisa estar entre 0 e 1")


class DisjuntorAberto(RuntimeError):
    """A fonte foi considerada indisponível e a chamada nem chegou a sair."""


class Disjuntor:
    """Para de bater na fonte depois de falhas seguidas e reabre por tempo.

    Estados: fechado (passa), aberto (recusa) e meio-aberto (deixa uma chamada
    de prova passar). Uma prova bem-sucedida fecha; uma prova falha reabre.
    """

    def __init__(
        self,
        *,
        falhas_para_abrir: int = 5,
        janela_de_recuperacao: float = 60.0,
        agora: Callable[[], float] = time.monotonic,
    ) -> None:
        if falhas_para_abrir < 1:
            raise ValueError("falhas_para_abrir precisa ser >= 1")
        self._falhas_para_abrir = falhas_para_abrir
        self._janela = janela_de_recuperacao
        self._agora = agora
        self._falhas = 0
        self._aberto_desde: float | None = None

    @property
    def estado(self) -> str:
        if self._aberto_desde is None:
            return "fechado"
        if self._agora() - self._aberto_desde >= self._janela:
            return "meio_aberto"
        return "aberto"

    def registrar_sucesso(self) -> None:
        self._falhas = 0
        self._aberto_desde = None

    def registrar_falha(self) -> None:
        self._falhas += 1
        if self._falhas >= self._falhas_para_abrir:
            self._aberto_desde = self._agora()
            logger.warning("disjuntor aberto", extra={"falhas": self._falhas})

    def executar(self, operacao: Callable[[], T]) -> T:
        if self.estado == "aberto":
            raise DisjuntorAberto("fonte indisponível: disjuntor aberto")
        try:
            resultado = operacao()
        except Exception:
            self.registrar_falha()
            raise
        self.registrar_sucesso()
        return resultado


def esperas(
    politica: PoliticaDeRetentativa, aleatorio: Callable[[], float] = random.random
) -> Iterator[float]:
    """Gera a espera de cada retentativa: exponencial, com teto e jitter."""
    espera = politica.espera_inicial
    for _ in range(politica.tentativas - 1):
        limitada = min(espera, politica.espera_maxima)
        yield limitada * (1 - politica.jitter + politica.jitter * aleatorio() * 2)
        espera *= politica.fator


def com_retentativa(
    operacao: Callable[[], T],
    *,
    politica: PoliticaDeRetentativa | None = None,
    excecoes: tuple[type[BaseException], ...] = (Exception,),
    dormir: Callable[[float], None] = time.sleep,
    aleatorio: Callable[[], float] = random.random,
    descricao: str = "operacao",
) -> T:
    """Executa `operacao` repetindo enquanto ela levantar `excecoes`.

    A última falha é relançada como está: engolir o erro depois de esgotar as
    tentativas transformaria indisponibilidade em resultado vazio silencioso.
    """
    politica = politica or PoliticaDeRetentativa()
    atrasos = list(esperas(politica, aleatorio))
    for tentativa in range(1, politica.tentativas + 1):
        try:
            return operacao()
        except DisjuntorAberto:
            raise
        except excecoes as erro:
            if tentativa == politica.tentativas:
                logger.error(
                    "operacao falhou em definitivo",
                    extra={"descricao": descricao, "tentativas": tentativa},
                )
                raise
            espera = atrasos[tentativa - 1]
            logger.warning(
                "retentando",
                extra={
                    "descricao": descricao,
                    "tentativa": tentativa,
                    "espera": round(espera, 3),
                    "erro": type(erro).__name__,
                },
            )
            dormir(espera)
    raise AssertionError("inalcançável")  # pragma: no cover


def chave_de_idempotencia(*partes: object) -> str:
    """Chave estável derivada do conteúdo das partes.

    Sem relógio e sem aleatoriedade: a mesma operação repetida tem que produzir
    a mesma chave, senão a proteção contra duplicidade não existe.
    """
    material = "".join(str(parte) for parte in partes)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
