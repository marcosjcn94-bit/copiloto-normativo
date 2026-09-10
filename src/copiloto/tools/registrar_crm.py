"""Tool 3: registro da consulta no CRM (§7 do briefing). Escrita — passa por HITL.

Duas funções, e a separação é o ponto:

* `propor_registro` é determinística e sem I/O. É tudo o que o agente pode fazer
  sozinho: montar o payload e a chave de idempotência.
* `enviar_registro` só roda depois que um humano aprovou a proposta (o
  `interrupt()` da Fase 5). Autonomia nível 2: o agente lê e propõe, quem escreve
  fora do sistema é uma decisão humana.

A `idempotency_key` sai de `thread_id + passo + argumentos`, nunca do relógio.
O modelo pode reemitir a mesma chamada depois de um timeout de rede, e o
aprovador humano pode clicar duas vezes — nos dois casos a chave colide e o CRM
reconhece o reenvio em vez de abrir um segundo registro.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from copiloto.resiliencia import (
    Disjuntor,
    DisjuntorAberto,
    PoliticaDeRetentativa,
    chave_de_idempotencia,
    com_retentativa,
)
from copiloto.tools.schemas import (
    EntradaRegistrarCrm,
    PropostaDeRegistro,
    SaidaRegistrarCrm,
)

logger = logging.getLogger(__name__)

NOME = "registrar_crm"

DESCRICAO = (
    "Propõe o registro de uma consulta no CRM, com o resumo do que foi respondido e as "
    "normas citadas. A proposta NÃO é gravada pelo agente: ela é apresentada a um humano, "
    "que aprova ou recusa antes de qualquer escrita. "
    "NÃO faz: não grava sozinha, não altera nem apaga registro existente, não consulta o "
    "CRM e não deve receber parecer, recomendação ou afirmação de conformidade no resumo."
)


class ErroDeCrm(RuntimeError):
    """O CRM não confirmou a escrita. Nunca é convertido em sucesso silencioso."""


def propor_registro(
    entrada: EntradaRegistrarCrm, *, thread_id: str, passo: int
) -> PropostaDeRegistro:
    """Monta a proposta e a chave de idempotência. Sem rede, sem efeito colateral."""
    chave = chave_de_idempotencia(
        thread_id,
        passo,
        entrada.model_dump_json(),
    )
    return PropostaDeRegistro(
        entrada=entrada,
        thread_id=thread_id,
        passo=passo,
        idempotency_key=chave,
    )


def enviar_registro(
    proposta: PropostaDeRegistro,
    *,
    cliente: httpx.Client,
    base_url: str,
    politica: PoliticaDeRetentativa | None = None,
    disjuntor: Disjuntor | None = None,
) -> SaidaRegistrarCrm:
    """Grava no CRM. Só chame com aprovação humana registrada (Fase 5)."""
    url = f"{base_url.rstrip('/')}/registros"
    corpo = _payload(proposta)
    cabecalhos = {"Idempotency-Key": proposta.idempotency_key}
    protecao = disjuntor or Disjuntor()

    def uma_tentativa() -> httpx.Response:
        resposta = cliente.post(url, json=corpo, headers=cabecalhos)
        if resposta.status_code >= 500:
            raise ErroDeCrm(f"crm respondeu {resposta.status_code}")
        return resposta

    try:
        resposta = protecao.executar(
            lambda: com_retentativa(
                uma_tentativa,
                politica=politica or PoliticaDeRetentativa(),
                excecoes=(ErroDeCrm, httpx.HTTPError),
                descricao="crm.registros",
            )
        )
    except DisjuntorAberto as erro:
        raise ErroDeCrm("crm indisponível: disjuntor aberto") from erro
    except httpx.HTTPError as erro:
        raise ErroDeCrm(f"falha de rede ao chamar o crm: {erro}") from erro

    # 409 é o CRM dizendo "essa chave já entrou": reenvio reconhecido, não falha.
    if resposta.status_code == 409:
        logger.info("registro duplicado reconhecido", extra={"chave": proposta.idempotency_key})
        return _para_saida(resposta, proposta, duplicado=True)
    if resposta.status_code >= 400:
        raise ErroDeCrm(f"crm recusou o registro: {resposta.status_code}")
    return _para_saida(resposta, proposta, duplicado=False)


def _payload(proposta: PropostaDeRegistro) -> dict[str, Any]:
    """O corpo que vai ao CRM — a chave viaja no header e no corpo, de propósito.

    No header porque é assim que um proxy ou o próprio CRM deduplica antes de
    tocar no banco; no corpo porque o registro gravado precisa carregar a chave
    para que a conferência da Fase 6 não dependa de log de servidor.
    """
    entrada = proposta.entrada
    return {
        "id_cliente": entrada.id_cliente,
        "assunto": entrada.assunto,
        "resumo": entrada.resumo,
        "normas_citadas": list(entrada.normas_citadas),
        "canal": entrada.canal,
        "thread_id": proposta.thread_id,
        "passo": proposta.passo,
        "idempotency_key": proposta.idempotency_key,
    }


def _para_saida(
    resposta: httpx.Response, proposta: PropostaDeRegistro, *, duplicado: bool
) -> SaidaRegistrarCrm:
    try:
        dados = resposta.json()
    except ValueError:
        dados = {}
    return SaidaRegistrarCrm(
        registrado=True,
        id_registro=str(dados.get("id") or dados.get("id_registro") or ""),
        idempotency_key=proposta.idempotency_key,
        duplicado=duplicado or bool(dados.get("duplicado")),
    )
