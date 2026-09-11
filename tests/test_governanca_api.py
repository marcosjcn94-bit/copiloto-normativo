"""Fase 9 — os quatro endpoints de `/governanca/*` montados no app real (§16.4).

Duplo de `Copiloto`, como em `test_api.py`: o que se mede aqui é a fronteira
HTTP dos endpoints de governança, que não dependem do grafo nem de índice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_api import CopilotoDeMentira

from copiloto.api.main import criar_app


def test_ficha_responde_200_com_o_id_do_sistema() -> None:
    with TestClient(criar_app(CopilotoDeMentira())) as cliente:
        resposta = cliente.get("/governanca/ficha")

    assert resposta.status_code == 200
    assert resposta.json()["identificacao"]["id"] == "copiloto-normativo-bcb"


def test_inventario_lista_groq_e_o_juiz() -> None:
    with TestClient(criar_app(CopilotoDeMentira())) as cliente:
        resposta = cliente.get("/governanca/inventario")

    ids = {s["id_sistema"] for s in resposta.json()["sistemas"]}
    assert {"groq", "eval-judge/camada-3"} <= ids


def test_metricas_responde_200_mesmo_sem_execucao_recente_de_evals() -> None:
    with TestClient(criar_app(CopilotoDeMentira())) as cliente:
        resposta = cliente.get("/governanca/metricas")

    assert resposta.status_code == 200
    assert "metricas" in resposta.json() and "roi" in resposta.json()


def test_coerencia_responde_200_no_estado_atual_do_repositorio() -> None:
    with TestClient(criar_app(CopilotoDeMentira())) as cliente:
        resposta = cliente.get("/governanca/coerencia")

    assert resposta.status_code == 200
    assert resposta.json()["divergencias"] == []
