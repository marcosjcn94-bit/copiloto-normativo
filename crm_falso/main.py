"""CRM falso — o destino da única escrita externa do sistema (Fase 6).

Não é mock de teste: é um processo separado, com HTTP de verdade no meio. O que
ele existe para provar é que a ponta de escrita do copiloto funciona contra um
serviço que ele não controla — inclusive quando o pedido chega duas vezes.

**Idempotência é o contrato, não um extra.** `registrar_crm.py` calcula a
`idempotency_key` a partir de `thread_id + passo + argumentos`, nunca do relógio.
Aqui essa chave é a identidade do registro: chave nova abre registro (201), chave
repetida devolve **409 com o registro que já existe** — nunca um segundo. O
agente pode reemitir depois de um timeout e o aprovador humano pode clicar duas
vezes; nos dois casos o CRM tem um registro só.

**Por que 409 devolve o corpo do registro, e não `detail`.** O cliente
(`enviar_registro`) lê `id` da resposta para preencher `SaidaRegistrarCrm`. Se o
409 viesse embrulhado no `detail` padrão do FastAPI, o reenvio reconhecido
chegaria ao usuário como registro gravado sem identificador. O formato desta
resposta é parte do contrato entre os dois processos.

**Estado em memória, de propósito.** Um CRM falso que persiste em disco convida a
depurar o CRM falso. Reiniciar o processo zera — e o `GET /registros` é o que
torna o resultado da demo visível sem ler log de servidor.

Como subir:

    .venv/Scripts/python.exe -m uvicorn crm_falso.main:criar_app --factory --port 8001
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Os mesmos padrões do §7 das tools, repetidos aqui de propósito: este é outro
# serviço. Importar o pacote do copiloto para validar o corpo faria o "CRM de
# terceiro" compartilhar tipos com quem o chama, e o teste de contrato entre os
# dois deixaria de medir alguma coisa.
PADRAO_CLIENTE = r"^[A-Z]{2,4}-\d{4,8}$"
PADRAO_THREAD = r"^[A-Za-z0-9_-]{8,64}$"
PADRAO_CHAVE = r"^[0-9a-f]{32}$"


class RegistroRecebido(BaseModel):
    """O corpo que `registrar_crm._payload` emite. Campo a mais reprova."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id_cliente: Annotated[str, Field(pattern=PADRAO_CLIENTE)]
    assunto: Annotated[str, Field(min_length=5, max_length=120)]
    resumo: Annotated[str, Field(min_length=20, max_length=1200)]
    normas_citadas: Annotated[list[str], Field(min_length=1, max_length=10)]
    canal: Literal["chat", "email", "telefone", "webhook"] = "chat"
    thread_id: Annotated[str, Field(pattern=PADRAO_THREAD)]
    passo: int = Field(ge=1, le=99)
    idempotency_key: Annotated[str, Field(pattern=PADRAO_CHAVE)]


class Registros:
    """A tabela do CRM: chave de idempotência -> registro gravado.

    O `Lock` não é zelo excessivo. O uvicorn atende endpoint síncrono em thread de
    worker, então dois pedidos com a mesma chave podem entrar ao mesmo tempo — é
    exatamente o duplo clique do aprovador. Sem o lock, os dois passariam pelo
    teste de existência antes de qualquer inserção e o CRM abriria dois registros.
    """

    def __init__(self) -> None:
        self._por_chave: dict[str, dict[str, Any]] = {}
        self._trava = threading.Lock()
        self._proximo = 1

    def gravar(self, recebido: RegistroRecebido) -> tuple[dict[str, Any], bool]:
        """Devolve `(registro, duplicado)`. Duplicado nunca cria linha nova."""
        with self._trava:
            existente = self._por_chave.get(recebido.idempotency_key)
            if existente is not None:
                return existente | {"duplicado": True}, True
            registro = {
                "id": f"reg-{self._proximo:04d}",
                "criado_em": datetime.now(UTC).isoformat(timespec="seconds"),
                "duplicado": False,
                **recebido.model_dump(),
            }
            self._proximo += 1
            self._por_chave[recebido.idempotency_key] = registro
            return registro, False

    def listar(self, *, thread_id: str | None = None) -> list[dict[str, Any]]:
        with self._trava:
            todos = list(self._por_chave.values())
        if thread_id is None:
            return todos
        return [r for r in todos if r["thread_id"] == thread_id]

    def por_id(self, id_registro: str) -> dict[str, Any] | None:
        with self._trava:
            for registro in self._por_chave.values():
                if registro["id"] == id_registro:
                    return registro
        return None


def criar_app(registros: Registros | None = None) -> FastAPI:
    """O app. `registros` entra por parâmetro para o teste começar com a tabela vazia."""
    app = FastAPI(
        title="CRM falso",
        version="1.0.0",
        summary="Imitação mínima de CRM para a Fase 6 — grava consulta e deduplica por chave.",
    )
    app.state.registros = registros or Registros()

    @app.get("/saude")
    def saude(request: Request) -> dict[str, Any]:
        return {"status": "ok", "registros": len(request.app.state.registros.listar())}

    @app.post("/registros", status_code=201)
    def criar_registro(
        recebido: RegistroRecebido,
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        # A chave viaja no header e no corpo (ver `registrar_crm._payload`). Se as
        # duas divergirem, quem deduplica no caminho e quem grava usariam chaves
        # diferentes: é erro de contrato, não pedido a ser interpretado.
        if idempotency_key is not None and idempotency_key != recebido.idempotency_key:
            return JSONResponse(
                status_code=400,
                content={"erro": "chave_divergente", "header": idempotency_key},
            )

        registro, duplicado = request.app.state.registros.gravar(recebido)
        if duplicado:
            logger.info(
                "registro duplicado recusado",
                extra={"chave": recebido.idempotency_key, "id": registro["id"]},
            )
            return JSONResponse(status_code=409, content=registro)
        logger.info(
            "registro criado", extra={"id": registro["id"], "thread_id": registro["thread_id"]}
        )
        return JSONResponse(status_code=201, content=registro)

    @app.get("/registros")
    def listar_registros(request: Request, thread_id: str | None = None) -> dict[str, Any]:
        """A janela da demo: mostra o que a execução do n8n gravou, por thread."""
        encontrados = request.app.state.registros.listar(thread_id=thread_id)
        return {"total": len(encontrados), "registros": encontrados}

    @app.get("/registros/{id_registro}")
    def obter_registro(id_registro: str, request: Request) -> JSONResponse:
        registro = request.app.state.registros.por_id(id_registro)
        if registro is None:
            return JSONResponse(status_code=404, content={"erro": "registro_inexistente"})
        return JSONResponse(status_code=200, content=registro)

    return app
