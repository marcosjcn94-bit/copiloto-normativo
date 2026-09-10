"""O estado tipado do agente e a serialização que ele precisa sobreviver.

**Por que `TypedDict` e não `BaseModel`.** É o canal do LangGraph: cada chave é
um slot que o nó atualiza parcialmente, e o checkpointer grava chave a chave.
Nenhum nó inventa chave — o que não está declarado aqui não existe no grafo.

**Por que a transcrição vira dicionário.** `Mensagem` é `dataclass` com `slots`,
e o que precisa atravessar um reinício de processo é o conteúdo, não a classe.
Guardar dicionário puro no checkpoint torna a retomada independente de qualquer
mudança futura na camada de LLM: o estado gravado hoje continua legível depois de
o `ProvedorLLM` ganhar campo novo. A conversão é de ida e volta e é testada.

**O que o estado NÃO guarda.** Provedor, registro de tools e cliente de CRM são
dependência, não estado: entram pelos nós (`Dependencias` em `grafo.py`). Estado
que carrega conexão não sobrevive ao checkpoint — e é justamente sobreviver ao
checkpoint que a Fase 5 tem de provar.
"""

from __future__ import annotations

from typing import Any, TypedDict

from copiloto.llm.provedor import ChamadaDeTool, Mensagem, Papel


class EstadoDoAgente(TypedDict, total=False):
    """O que atravessa o grafo, e só isso.

    `encerramento` é o campo que as arestas condicionais leem para decidir se a
    execução acabou; enquanto ele está vazio, o grafo continua. Os valores
    possíveis são `respondida`, `bloqueada_na_entrada`, `sem_base_normativa`,
    `limite_de_passos` e `falha_de_tool`.
    """

    # entrada
    pergunta: str  # já saneada — o guardrail de entrada roda antes de qualquer nó de LLM
    pergunta_original: str
    thread_id: str
    achados_pii: list[str]

    # deliberação
    passo: int
    mensagens: list[dict[str, Any]]
    trechos: list[dict[str, Any]]  # `TrechoCitado` serializado, acumulado por execução

    # resposta e verificação
    resposta: str
    citacoes: list[str]
    tentativa_resposta: int

    # escrita externa (HITL)
    proposta: dict[str, Any] | None
    aprovacao: dict[str, Any] | None
    registro: dict[str, Any] | None

    # término
    encerramento: str
    motivo: str


def para_dicionario(mensagem: Mensagem) -> dict[str, Any]:
    """`Mensagem` -> dicionário puro, do jeito que o checkpointer grava."""
    return {
        "papel": mensagem.papel,
        "conteudo": mensagem.conteudo,
        "chamadas": [
            {"id": c.id, "nome": c.nome, "argumentos": c.argumentos} for c in mensagem.chamadas
        ],
        "id_chamada": mensagem.id_chamada,
    }


def de_dicionario(dados: dict[str, Any]) -> Mensagem:
    """Dicionário -> `Mensagem`. O par de `para_dicionario`, sem perda."""
    papel: Papel = dados["papel"]
    return Mensagem(
        papel=papel,
        conteudo=dados.get("conteudo", ""),
        chamadas=tuple(
            ChamadaDeTool(id=c["id"], nome=c["nome"], argumentos=c["argumentos"])
            for c in dados.get("chamadas", ())
        ),
        id_chamada=dados.get("id_chamada"),
    )


def transcricao(estado: EstadoDoAgente) -> list[Mensagem]:
    """A conversa gravada no estado, de volta ao tipo que o provedor entende."""
    return [de_dicionario(m) for m in estado.get("mensagens", [])]


def gravar(mensagens: list[Mensagem]) -> list[dict[str, Any]]:
    """A conversa no formato do checkpoint."""
    return [para_dicionario(m) for m in mensagens]
