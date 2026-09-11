"""Os quatro endpoints de `/governanca/*` (§16.4 do briefing).

Router à parte de `api/rotas.py` de propósito: a API do copiloto depende de
`Copiloto` (grafo, índices, CRM); esta depende só do sistema de arquivos —
`config/governanca.yaml`, `parametros.toml` e o código de `llm/`. Montar os
dois no mesmo router acoplaria coisas que hoje não precisam se conhecer.

**Sem estado entre requisições.** Cada chamada lê a ficha e recalcula a
coerência na hora — não há cache. `/governanca/coerencia` roda dois testes
via subprocess (`coerencia.py::verificar_controles_com_prova`), então custa
mais que os outros três; é o preço de "o teste existe e passa" significar o
teste de verdade, não uma cópia do resultado.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from copiloto.governanca.coerencia import verificar
from copiloto.governanca.ficha import carregar_ficha
from copiloto.governanca.inventario import inventario
from copiloto.governanca.metricas import carregar_resultados, metricas_do_painel, roi_estimado

logger = logging.getLogger(__name__)


def criar_router_governanca() -> APIRouter:
    router = APIRouter(prefix="/governanca", tags=["governanca"])

    @router.get("/ficha")
    def ficha() -> dict:
        return carregar_ficha().model_dump(mode="json")

    @router.get("/inventario")
    def inventario_da_frota() -> dict:
        f = carregar_ficha()
        return inventario(modelos_da_ficha=tuple(f.modelos))

    @router.get("/metricas")
    def metricas() -> dict:
        resultados = carregar_resultados()
        return {
            "metricas": metricas_do_painel(resultados),
            "roi": roi_estimado(resultados),
        }

    @router.get("/coerencia")
    def coerencia() -> JSONResponse:
        f = carregar_ficha()
        divergencias = verificar(f)
        corpo = {"divergencias": [d.como_dicionario() for d in divergencias]}
        if divergencias:
            logger.warning("ficha divergente do código", extra={"total": len(divergencias)})
        return JSONResponse(status_code=200 if not divergencias else 409, content=corpo)

    return router
