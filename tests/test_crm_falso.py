"""Fase 6 — o CRM falso e o contrato entre ele e `registrar_crm.py`.

Os testes de `test_tools.py` já exercitam `enviar_registro` contra um
`MockTransport`, onde o formato da resposta é o que o teste escreveu. Aqui é o
contrário: a resposta vem do serviço de verdade, pelo ASGI, e o que se mede é se
os dois lados concordam sobre o que é um 201, o que é um 409 e onde vive o `id`.
Um mock que concorda consigo mesmo não descobre divergência de contrato.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from copiloto.tools.registrar_crm import enviar_registro, propor_registro
from copiloto.tools.schemas import EntradaRegistrarCrm
from crm_falso.main import Registros, criar_app

THREAD = "thread-de-teste"
CITACAO = "Resolução BCB nº 85, de 2021, art. 7º"


@pytest.fixture
def cliente() -> TestClient:
    """App novo por teste: a tabela do CRM é memória, e memória vaza entre testes."""
    return TestClient(criar_app(Registros()))


def corpo(**trocas: object) -> dict[str, object]:
    padrao: dict[str, object] = {
        "id_cliente": "ABC-12345",
        "assunto": "Política de segurança cibernética",
        "resumo": "Cliente perguntou sobre exigência de política; respondido com citação.",
        "normas_citadas": [CITACAO],
        "canal": "webhook",
        "thread_id": THREAD,
        "passo": 2,
        "idempotency_key": "0" * 32,
    }
    return padrao | trocas


# --- contrato do serviço -----------------------------------------------------


def test_saude_responde_com_a_contagem(cliente: TestClient) -> None:
    resposta = cliente.get("/saude")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "registros": 0}


def test_registro_novo_devolve_201_com_id(cliente: TestClient) -> None:
    resposta = cliente.post("/registros", json=corpo())

    assert resposta.status_code == 201
    dados = resposta.json()
    assert dados["id"] == "reg-0001"
    assert dados["duplicado"] is False
    assert dados["thread_id"] == THREAD


def test_chave_repetida_devolve_409_com_o_registro_que_ja_existe(cliente: TestClient) -> None:
    """O duplo clique do aprovador não pode abrir um segundo registro."""
    primeira = cliente.post("/registros", json=corpo())
    segunda = cliente.post("/registros", json=corpo(assunto="Outro assunto qualquer"))

    assert (primeira.status_code, segunda.status_code) == (201, 409)
    # O 409 carrega o registro original — inclusive o assunto da primeira chamada.
    assert segunda.json()["id"] == primeira.json()["id"]
    assert segunda.json()["duplicado"] is True
    assert segunda.json()["assunto"] == "Política de segurança cibernética"
    assert cliente.get("/registros").json()["total"] == 1


def test_chaves_diferentes_abrem_registros_diferentes(cliente: TestClient) -> None:
    cliente.post("/registros", json=corpo())
    cliente.post("/registros", json=corpo(idempotency_key="1" * 32))

    assert cliente.get("/registros").json()["total"] == 2


def test_header_divergente_do_corpo_e_erro_de_contrato(cliente: TestClient) -> None:
    resposta = cliente.post("/registros", json=corpo(), headers={"Idempotency-Key": "9" * 32})

    assert resposta.status_code == 400
    assert resposta.json()["erro"] == "chave_divergente"
    assert cliente.get("/registros").json()["total"] == 0


def test_campo_a_mais_reprova(cliente: TestClient) -> None:
    resposta = cliente.post("/registros", json=corpo(parecer="pode operar"))

    assert resposta.status_code == 422


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("id_cliente", "cliente-1"),
        ("thread_id", "curto"),
        ("idempotency_key", "nao-e-hexa"),
        ("passo", 0),
        ("normas_citadas", []),
    ],
)
def test_corpo_malformado_reprova(cliente: TestClient, campo: str, valor: object) -> None:
    assert cliente.post("/registros", json=corpo(**{campo: valor})).status_code == 422


def test_lista_filtra_por_thread(cliente: TestClient) -> None:
    """A janela da demo: ver o que aquela execução do n8n gravou, e só ela."""
    cliente.post("/registros", json=corpo())
    cliente.post("/registros", json=corpo(idempotency_key="1" * 32, thread_id="outra-thread-1"))

    assert cliente.get("/registros", params={"thread_id": THREAD}).json()["total"] == 1


def test_registro_inexistente_devolve_404(cliente: TestClient) -> None:
    assert cliente.get("/registros/reg-9999").status_code == 404


# --- contrato entre a tool e o serviço ---------------------------------------


def proposta_de_teste():
    entrada = EntradaRegistrarCrm(
        id_cliente="ABC-12345",
        assunto="Política de segurança cibernética",
        resumo="Cliente perguntou sobre exigência de política; respondido com citação.",
        normas_citadas=(CITACAO,),
        canal="webhook",
    )
    return propor_registro(entrada, thread_id=THREAD, passo=2)


def test_enviar_registro_grava_no_crm_de_verdade() -> None:
    """Sem mock no meio: a tool fala HTTP com o serviço da Fase 6.

    `TestClient` é subclasse de `httpx.Client`, então é ele que entra no parâmetro
    `cliente` de `enviar_registro` — o mesmo lugar onde, em produção, entra o
    cliente de `cliente_de_crm`. O que muda é só o transporte.
    """
    proposta = proposta_de_teste()

    with TestClient(criar_app(Registros())) as cliente:
        base = str(cliente.base_url)
        saida = enviar_registro(proposta, cliente=cliente, base_url=base)

        assert saida.registrado is True
        assert saida.id_registro == "reg-0001"
        assert saida.duplicado is False
        assert saida.idempotency_key == proposta.idempotency_key

        # E o reenvio da mesma proposta é reconhecido, não recusado nem duplicado.
        repetida = enviar_registro(proposta, cliente=cliente, base_url=base)
        assert (repetida.duplicado, repetida.id_registro) == (True, "reg-0001")
        assert cliente.get("/registros").json()["total"] == 1
