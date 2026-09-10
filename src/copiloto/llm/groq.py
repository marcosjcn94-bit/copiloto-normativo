"""Provedor primário: Groq, API no estilo OpenAI (§3.2 do briefing).

**Por que não é o GitHub Models.** O briefing elegeu o GitHub Models como
provedor gratuito primário. Em 30/07/2026 a GitHub encerrou o produto inteiro —
playground, catálogo e API de inferência —, e o endpoint novo
(`models.github.ai/inference`) morreu junto com o antigo. A troca custou este
arquivo e nada mais: o grafo, as tools e os testes falam com o `Protocol` de
`provedor.py`. Esse é o argumento da camada de provedor, e agora ele tem prova.

**E por que não é mais o Llama 3.3.** Em 10/09/2026, durante a Fase 7, o
`llama-3.3-70b-versatile` sumiu do catálogo da Groq e passou a devolver 404. O
sintoma chegou disfarçado: `ErroDeProvedor: disjuntor aberto`, porque o
`resiliencia.py` abriu o circuito depois das tentativas. **Um 404 de modelo
inexistente não é falha transitória e não deveria consumir retentativa** — está
anotado como a próxima correção deste arquivo, e a Fase 7 o encontrou porque a
camada 3 dos evals exercita o provedor de ponta a ponta.

A API é compatível com a da OpenAI, então `azure_openai.py` reusa a mesma
tradução de mensagens; só mudam a URL e o header de autenticação.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from copiloto.llm.provedor import (
    ChamadaDeTool,
    ErroDeProvedor,
    EspecificacaoDeTool,
    Mensagem,
    RespostaLLM,
    Uso,
)
from copiloto.resiliencia import (
    Disjuntor,
    DisjuntorAberto,
    PoliticaDeRetentativa,
    com_retentativa,
)

logger = logging.getLogger(__name__)

ENDPOINT_PADRAO = "https://api.groq.com/openai/v1"
# O `llama-3.3-70b-versatile` saiu do catálogo da Groq: em 10/09/2026 ele passou a
# responder 404 `model_not_found`, e com ele o copiloto parou de responder. É a
# **segunda** morte de fornecedor deste projeto, depois do GitHub Models em
# 30/07/2026 — e as duas custaram só este arquivo, que é o argumento da camada de
# provedor do §3.2 medido em incidentes reais e não em intenção.
#
# O substituto foi escolhido testando o catálogo vivo com tool calling de verdade,
# não pelo nome: `openai/gpt-oss-20b` respondeu sem chamar a tool e `groq/compound`
# recusa `tools` com HTTP 400. Um modelo que não chama tool não serve a este agente,
# e isso não aparece na ficha técnica de nenhum deles.
MODELO_PADRAO = "openai/gpt-oss-120b"

# O modelo pode devolver `arguments` que não é JSON válido. O texto cru é
# preservado sob esta chave em vez de virar exceção: com `extra='forbid'` no
# schema, ele reprova na validação e alimenta o laço de autocorreção — que é
# onde o erro do modelo deve ser tratado, não aqui.
CHAVE_JSON_INVALIDO = "__argumentos_nao_json__"


class _ErroPermanente(RuntimeError):
    """4xx: sai do laço de retentativa em vez de queimar cota."""


class ProvedorGroq:
    """Cliente HTTP do endpoint de chat completions da Groq.

    Rede é tratada por `resiliencia.py`, nunca por `try/except` improvisado:
    5xx e 429 entram no backoff; os demais 4xx não, porque repetir um pedido
    malformado só gasta cota.
    """

    id_sistema = "groq"

    def __init__(
        self,
        *,
        chave: str,
        modelo: str = MODELO_PADRAO,
        endpoint: str = ENDPOINT_PADRAO,
        cliente: httpx.Client | None = None,
        politica: PoliticaDeRetentativa | None = None,
        disjuntor: Disjuntor | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not chave:
            raise ValueError("GROQ_API_KEY ausente: o provedor não sobe sem credencial")
        self._chave = chave
        self._modelo = modelo
        self._endpoint = endpoint.rstrip("/")
        self._cliente = cliente
        self._politica = politica or PoliticaDeRetentativa()
        self._disjuntor = disjuntor or Disjuntor()
        self._timeout = timeout

    @classmethod
    def do_ambiente(cls, ambiente: Mapping[str, str] | None = None, **extras: Any) -> ProvedorGroq:
        """Monta o provedor a partir do `.env`. Falta de chave falha no boot."""
        amb = ambiente if ambiente is not None else os.environ
        return cls(
            chave=amb.get("GROQ_API_KEY", ""),
            modelo=amb.get("GROQ_MODEL", MODELO_PADRAO),
            endpoint=amb.get("GROQ_ENDPOINT", ENDPOINT_PADRAO),
            **extras,
        )

    @property
    def modelo(self) -> str:
        return self._modelo

    def gerar(
        self,
        mensagens: Sequence[Mensagem],
        *,
        tools: Sequence[EspecificacaoDeTool] = (),
        temperatura: float | None = None,
    ) -> RespostaLLM:
        corpo: dict[str, Any] = {
            "model": self._modelo,
            "messages": [mensagem_para_openai(m) for m in mensagens],
        }
        if tools:
            corpo["tools"] = [tool_para_openai(t) for t in tools]
            corpo["tool_choice"] = "auto"
        if temperatura is not None:
            corpo["temperature"] = temperatura
        return self._interpretar(self._postar(corpo))

    def _postar(self, corpo: dict[str, Any]) -> Mapping[str, Any]:
        url = f"{self._endpoint}/chat/completions"
        cabecalhos = {
            "Authorization": f"Bearer {self._chave}",
            "Content-Type": "application/json",
        }

        def uma_tentativa() -> Mapping[str, Any]:
            if self._cliente is None:
                with httpx.Client(timeout=self._timeout) as efemero:
                    resposta = efemero.post(url, json=corpo, headers=cabecalhos)
            else:
                resposta = self._cliente.post(url, json=corpo, headers=cabecalhos)
            if resposta.status_code == 429 or resposta.status_code >= 500:
                raise ErroDeProvedor(f"groq respondeu {resposta.status_code}")
            if resposta.status_code >= 400:
                raise _ErroPermanente(f"groq recusou o pedido: {resposta.status_code}")
            return resposta.json()

        try:
            return self._disjuntor.executar(
                lambda: com_retentativa(
                    uma_tentativa,
                    politica=self._politica,
                    excecoes=(ErroDeProvedor, httpx.HTTPError),
                    descricao="groq.chat.completions",
                )
            )
        except DisjuntorAberto as erro:
            raise ErroDeProvedor("groq indisponível: disjuntor aberto") from erro
        except _ErroPermanente as erro:
            raise ErroDeProvedor(str(erro)) from erro
        except httpx.HTTPError as erro:
            raise ErroDeProvedor(f"falha de rede ao chamar groq: {erro}") from erro

    def _interpretar(self, dados: Mapping[str, Any]) -> RespostaLLM:
        escolhas = dados.get("choices") or []
        if not escolhas:
            raise ErroDeProvedor("resposta da groq sem `choices`")
        escolha = escolhas[0]
        mensagem = escolha.get("message") or {}
        uso = dados.get("usage") or {}
        return RespostaLLM(
            conteudo=mensagem.get("content") or "",
            chamadas=tuple(_chamadas(mensagem.get("tool_calls") or [])),
            modelo=dados.get("model") or self._modelo,
            id_sistema=self.id_sistema,
            uso=Uso(
                tokens_entrada=int(uso.get("prompt_tokens") or 0),
                tokens_saida=int(uso.get("completion_tokens") or 0),
            ),
            motivo_parada=escolha.get("finish_reason") or "",
        )


def _chamadas(brutas: Sequence[Mapping[str, Any]]) -> list[ChamadaDeTool]:
    return [
        ChamadaDeTool(
            id=str(bruta.get("id") or ""),
            nome=str((bruta.get("function") or {}).get("name") or ""),
            argumentos=_argumentos((bruta.get("function") or {}).get("arguments")),
        )
        for bruta in brutas
    ]


def _argumentos(bruto: Any) -> dict[str, Any]:
    """Converte o `arguments` da API em dict, sem esconder JSON quebrado."""
    if isinstance(bruto, dict):
        return bruto
    if not bruto:
        return {}
    try:
        interpretado = json.loads(bruto)
    except json.JSONDecodeError:
        logger.warning("modelo devolveu arguments fora do JSON")
        return {CHAVE_JSON_INVALIDO: str(bruto)}
    return interpretado if isinstance(interpretado, dict) else {CHAVE_JSON_INVALIDO: str(bruto)}


def mensagem_para_openai(mensagem: Mensagem) -> dict[str, Any]:
    """Traduz uma `Mensagem` para o formato de `messages` da API OpenAI."""
    corpo: dict[str, Any] = {"role": mensagem.papel, "content": mensagem.conteudo}
    if mensagem.papel == "tool":
        corpo["tool_call_id"] = mensagem.id_chamada or ""
    if mensagem.chamadas:
        corpo["tool_calls"] = [
            {
                "id": chamada.id,
                "type": "function",
                "function": {
                    "name": chamada.nome,
                    "arguments": json.dumps(chamada.argumentos, ensure_ascii=False),
                },
            }
            for chamada in mensagem.chamadas
        ]
    return corpo


def tool_para_openai(tool: EspecificacaoDeTool) -> dict[str, Any]:
    """Traduz a especificação de tool para o bloco `tools` da API."""
    return {
        "type": "function",
        "function": {
            "name": tool.nome,
            "description": tool.descricao,
            "parameters": tool.parametros,
        },
    }
