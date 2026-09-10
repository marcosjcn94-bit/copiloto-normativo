"""Fase 6 — o workflow do n8n como contrato, não como desenho bonito.

Um JSON de workflow é código que ninguém compila: erra o nome de um nó e a falha
só aparece na demo. Estes testes cobrem o que dá para conferir sem subir o n8n:

* toda conexão aponta para um nó que existe, e todo nó é alcançável;
* nenhuma URL é literal — host de container e porta vivem no ambiente;
* **os caminhos chamados existem na API de verdade**. É o teste que segura o
  acoplamento: renomear `/aprovar` no FastAPI derruba esta suíte, em vez de
  derrubar a apresentação.

O que ele não cobre: que o n8n de fato executa. Isso é a demo do §3.1 — sobe n8n
e CRM falso, dispara o webhook, derruba.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from copiloto.api.main import criar_app

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOW = RAIZ / "n8n" / "workflow-triagem.json"

TIPO_WEBHOOK = "n8n-nodes-base.webhook"
TIPO_HTTP = "n8n-nodes-base.httpRequest"
TIPO_RESPOSTA = "n8n-nodes-base.respondToWebhook"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def nos_por_tipo(workflow: dict[str, Any], tipo: str) -> list[dict[str, Any]]:
    return [no for no in workflow["nodes"] if no["type"] == tipo]


def urls(workflow: dict[str, Any]) -> list[str]:
    return [no["parameters"]["url"] for no in nos_por_tipo(workflow, TIPO_HTTP)]


# --- integridade do grafo do workflow ----------------------------------------


def test_json_valido_com_nomes_unicos(workflow: dict[str, Any]) -> None:
    nomes = [no["name"] for no in workflow["nodes"]]
    ids = [no["id"] for no in workflow["nodes"]]

    assert len(nomes) == len(set(nomes)), "nome repetido: a conexão do n8n é por nome"
    assert len(ids) == len(set(ids))
    assert workflow["settings"]["executionOrder"] == "v1"


def test_toda_conexao_aponta_para_no_existente(workflow: dict[str, Any]) -> None:
    nomes = {no["name"] for no in workflow["nodes"]}

    for origem, saidas in workflow["connections"].items():
        assert origem in nomes, f"conexão saindo de nó inexistente: {origem}"
        for ramo in saidas["main"]:
            for destino in ramo:
                assert destino["node"] in nomes, f"conexão para nó inexistente: {destino['node']}"


def test_nenhum_no_fica_orfao(workflow: dict[str, Any]) -> None:
    """Só webhook é ponto de entrada; qualquer outro nó solto é erro de fiação."""
    destinos = {
        destino["node"]
        for saidas in workflow["connections"].values()
        for ramo in saidas["main"]
        for destino in ramo
    }
    gatilhos = {no["name"] for no in nos_por_tipo(workflow, TIPO_WEBHOOK)}

    for no in workflow["nodes"]:
        assert no["name"] in destinos or no["name"] in gatilhos, f"nó órfão: {no['name']}"


def test_os_dois_gatilhos_sao_os_da_autonomia_nivel_2(workflow: dict[str, Any]) -> None:
    """Consultar e aprovar são entradas distintas — de propósito.

    Um único webhook que perguntasse e aprovasse na mesma execução seria autonomia
    nível 3 com etapa extra: a escrita aconteceria sem ninguém decidir nada.
    """
    webhooks = nos_por_tipo(workflow, TIPO_WEBHOOK)
    caminhos = {no["parameters"]["path"] for no in webhooks}

    assert caminhos == {"triagem", "triagem-aprovacao"}
    for no in webhooks:
        assert no["parameters"]["httpMethod"] == "POST"
        assert no["parameters"]["responseMode"] == "responseNode"


def test_cada_ramo_termina_em_resposta_ao_webhook(workflow: dict[str, Any]) -> None:
    """`responseMode: responseNode` sem nó de resposta deixa o chamador pendurado."""
    respostas = nos_por_tipo(workflow, TIPO_RESPOSTA)

    # Dois ramos do IF na consulta, mais o retorno da aprovação.
    assert len(respostas) == 3


def test_o_ramo_verdadeiro_do_if_e_o_que_pede_aprovacao(workflow: dict[str, Any]) -> None:
    condicao = next(no for no in workflow["nodes"] if no["type"] == "n8n-nodes-base.if")
    ramos = workflow["connections"][condicao["name"]]["main"]

    assert condicao["parameters"]["conditions"]["conditions"][0]["rightValue"] == (
        "aguardando_aprovacao"
    )
    assert "proposta" in ramos[0][0]["node"].lower()


# --- acoplamento com a API ---------------------------------------------------


def test_nenhuma_url_e_literal(workflow: dict[str, Any]) -> None:
    """Host de container não é constante de workflow.

    `COPILOTO_URL` e `CRM_URL` chegam pelo ambiente (ver `infra/docker-compose.yml`).
    URL literal aqui significa editar JSON para trocar de máquina — e esquecer de
    editar na hora da demo.
    """
    for url in urls(workflow):
        assert url.startswith("={{ $env."), url
        assert "http://" not in url and "https://" not in url, url


def test_os_caminhos_chamados_existem_na_api(workflow: dict[str, Any]) -> None:
    """O teste que amarra o workflow ao código, e não à lembrança de quem escreveu."""
    # Pelo OpenAPI, e não por `app.routes`: o esquema é o que a API declara
    # publicamente, e não muda de forma quando o FastAPI reorganiza roteadores.
    publicados = set(criar_app(_CopilotoInerte()).openapi()["paths"])
    chamados = {
        re.sub(r"^=\{\{ \$env\.[A-Z_]+ \}\}", "", url)
        for url in urls(workflow)
        if "COPILOTO_URL" in url
    }

    assert chamados == {"/perguntar", "/aprovar"}
    assert chamados <= publicados, f"workflow chama rota que a API não publica: {chamados}"


def test_a_consulta_ao_crm_le_o_proprio_crm(workflow: dict[str, Any]) -> None:
    """O critério de pronto pede o registro visível; ler o log da API não serve."""
    do_crm = [url for url in urls(workflow) if "CRM_URL" in url]

    assert do_crm == ["={{ $env.CRM_URL }}/registros"]


def test_a_decisao_enviada_carrega_revisor(workflow: dict[str, Any]) -> None:
    """A API recusa aprovação sem revisor; o workflow não pode omitir o campo."""
    no = next(
        n for n in nos_por_tipo(workflow, TIPO_HTTP) if n["parameters"]["url"].endswith("/aprovar")
    )
    campos = {p["name"] for p in no["parameters"]["bodyParameters"]["parameters"]}

    assert {"thread_id", "aprovado", "revisor"} <= campos


class _CopilotoInerte:
    """Só existe para `criar_app` ter o que guardar no `app.state`."""

    def nova_thread(self) -> str:
        raise AssertionError("este teste não executa requisição")

    def responder(self, pergunta: str, *, thread_id: str):
        raise AssertionError("este teste não executa requisição")

    def decidir(self, decisao):
        raise AssertionError("este teste não executa requisição")

    def saude(self):
        raise AssertionError("este teste não executa requisição")
