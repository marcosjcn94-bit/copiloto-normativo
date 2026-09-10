"""Camada de provedor de LLM (§3.2): tradução, parsing e política de erro.

Nada aqui toca a rede: o `httpx.MockTransport` responde no lugar da Groq. O que
está sob teste é o contrato — o que sobe no corpo, o que desce virando
`RespostaLLM` e quando repetir é certo ou errado.
"""

from __future__ import annotations

import json

import httpx
import pytest

from copiloto.llm.groq import (
    CHAVE_JSON_INVALIDO,
    ProvedorGroq,
    mensagem_para_openai,
    tool_para_openai,
)
from copiloto.llm.provedor import (
    ChamadaDeTool,
    ErroDeProvedor,
    EspecificacaoDeTool,
    Mensagem,
)
from copiloto.resiliencia import PoliticaDeRetentativa

SEM_ESPERA = PoliticaDeRetentativa(tentativas=3, espera_inicial=0.0, jitter=0.0)


def resposta_de_chat(
    *,
    conteudo: str = "",
    chamadas: list[dict] | None = None,
    modelo: str = "llama-3.3-70b-versatile",
) -> dict:
    mensagem: dict = {"role": "assistant", "content": conteudo}
    if chamadas is not None:
        mensagem["tool_calls"] = chamadas
    return {
        "model": modelo,
        "choices": [{"message": mensagem, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def provedor_com(handler, *, politica: PoliticaDeRetentativa | None = None) -> ProvedorGroq:
    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    return ProvedorGroq(chave="chave-de-teste", cliente=cliente, politica=politica or SEM_ESPERA)


def test_provedor_exige_credencial():
    """Sem chave o provedor não sobe: falhar no boot é melhor que 401 no meio."""
    with pytest.raises(ValueError):
        ProvedorGroq(chave="")


def test_gerar_monta_corpo_no_formato_openai():
    capturado: dict = {}

    def handler(requisicao: httpx.Request) -> httpx.Response:
        capturado.update(json.loads(requisicao.content))
        capturado["autorizacao"] = requisicao.headers["Authorization"]
        capturado["url"] = str(requisicao.url)
        return httpx.Response(200, json=resposta_de_chat(conteudo="ok"))

    tool = EspecificacaoDeTool(
        nome="buscar_normativo",
        descricao="busca",
        parametros={"type": "object", "properties": {}},
    )
    resposta = provedor_com(handler).gerar(
        [Mensagem.sistema("regras"), Mensagem.usuario("o que exige a norma?")],
        tools=[tool],
        temperatura=0.0,
    )

    assert capturado["url"].endswith("/openai/v1/chat/completions")
    assert capturado["autorizacao"] == "Bearer chave-de-teste"
    assert [m["role"] for m in capturado["messages"]] == ["system", "user"]
    assert capturado["tools"][0]["function"]["name"] == "buscar_normativo"
    assert capturado["tool_choice"] == "auto"
    assert resposta.conteudo == "ok"
    assert resposta.id_sistema == "groq"
    assert resposta.uso.tokens_entrada == 11


def test_sem_tools_o_corpo_nao_leva_o_bloco():
    """Mandar `tools: []` faz alguns provedores recusarem o pedido."""
    capturado: dict = {}

    def handler(requisicao: httpx.Request) -> httpx.Response:
        capturado.update(json.loads(requisicao.content))
        return httpx.Response(200, json=resposta_de_chat(conteudo="ok"))

    provedor_com(handler).gerar([Mensagem.usuario("oi, tudo certo?")])
    assert "tools" not in capturado
    assert "temperature" not in capturado


def test_chamadas_de_tool_viram_objetos_tipados():
    chamada = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "consultar_catalogo",
            "arguments": json.dumps({"tema": "computacao_em_nuvem"}),
        },
    }
    resposta = provedor_com(
        lambda _: httpx.Response(200, json=resposta_de_chat(chamadas=[chamada]))
    ).gerar([Mensagem.usuario("quais normas de nuvem?")])

    assert resposta.pediu_tool
    assert resposta.chamadas[0].nome == "consultar_catalogo"
    assert resposta.chamadas[0].argumentos == {"tema": "computacao_em_nuvem"}


def test_arguments_fora_do_json_nao_viram_excecao():
    """JSON quebrado do modelo é erro de schema, e quem julga isso é o Pydantic.

    Levantar aqui mataria o laço de autocorreção antes de ele existir; o texto
    cru segue preservado para reprovar na validação.
    """
    chamada = {"id": "c", "function": {"name": "buscar_normativo", "arguments": "{tema: nuvem"}}
    resposta = provedor_com(
        lambda _: httpx.Response(200, json=resposta_de_chat(chamadas=[chamada]))
    ).gerar([Mensagem.usuario("pergunta qualquer")])

    assert resposta.chamadas[0].argumentos[CHAVE_JSON_INVALIDO] == "{tema: nuvem"


def test_erro_5xx_e_repetido_e_o_sucesso_seguinte_vale():
    tentativas = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        tentativas["n"] += 1
        if tentativas["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=resposta_de_chat(conteudo="enfim"))

    assert provedor_com(handler).gerar([Mensagem.usuario("pergunta qualquer")]).conteudo == "enfim"
    assert tentativas["n"] == 3


def test_erro_4xx_nao_e_repetido():
    """Pedido malformado ou credencial inválida não melhora na segunda vez."""
    tentativas = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        tentativas["n"] += 1
        return httpx.Response(401)

    with pytest.raises(ErroDeProvedor):
        provedor_com(handler).gerar([Mensagem.usuario("pergunta qualquer")])
    assert tentativas["n"] == 1


def test_429_e_repetido_porque_e_cota_e_nao_erro_de_pedido():
    tentativas = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        tentativas["n"] += 1
        return (
            httpx.Response(429)
            if tentativas["n"] == 1
            else httpx.Response(200, json=resposta_de_chat(conteudo="ok"))
        )

    assert provedor_com(handler).gerar([Mensagem.usuario("pergunta qualquer")]).conteudo == "ok"
    assert tentativas["n"] == 2


def test_resposta_sem_choices_e_erro_de_provedor():
    with pytest.raises(ErroDeProvedor):
        provedor_com(lambda _: httpx.Response(200, json={"model": "x"})).gerar(
            [Mensagem.usuario("pergunta qualquer")]
        )


def test_mensagem_de_tool_carrega_o_id_da_chamada():
    corpo = mensagem_para_openai(Mensagem.resultado_de_tool("call_9", '{"total": 0}'))
    assert corpo == {"role": "tool", "content": '{"total": 0}', "tool_call_id": "call_9"}


def test_assistente_com_chamada_serializa_argumentos_como_texto():
    mensagem = Mensagem.assistente("", [ChamadaDeTool(id="c1", nome="t", argumentos={"a": 1})])
    corpo = mensagem_para_openai(mensagem)
    assert json.loads(corpo["tool_calls"][0]["function"]["arguments"]) == {"a": 1}


def test_especificacao_de_tool_vira_bloco_function():
    bloco = tool_para_openai(EspecificacaoDeTool(nome="t", descricao="d", parametros={"a": 1}))
    assert bloco == {
        "type": "function",
        "function": {"name": "t", "description": "d", "parameters": {"a": 1}},
    }


def test_do_ambiente_le_endpoint_e_modelo():
    provedor = ProvedorGroq.do_ambiente({"GROQ_API_KEY": "k", "GROQ_MODEL": "llama-3.1-8b-instant"})
    assert provedor.modelo == "llama-3.1-8b-instant"


@pytest.mark.rede
def test_ciclo_real_com_a_groq():
    """Fumaça contra a API de verdade. Roda só quando há chave no ambiente.

    Não mede qualidade do modelo — mede que a credencial, a URL e o formato do
    bloco `tools` continuam de pé. É o teste que avisa se a Groq mudar o que a
    GitHub mudou.
    """
    import os

    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY ausente")

    tool = EspecificacaoDeTool(
        nome="consultar_catalogo",
        descricao="Lista normas por metadado.",
        parametros={
            "type": "object",
            "properties": {"tema": {"type": "string", "enum": ["computacao_em_nuvem"]}},
            "required": ["tema"],
            "additionalProperties": False,
        },
    )
    resposta = ProvedorGroq.do_ambiente().gerar(
        [
            Mensagem.sistema("Use as tools disponíveis para responder."),
            Mensagem.usuario("quais normas sobre computação em nuvem existem?"),
        ],
        tools=[tool],
        temperatura=0.0,
    )
    assert resposta.id_sistema == "groq"
    assert resposta.conteudo or resposta.pediu_tool
