"""O contrato que todo provedor de LLM cumpre (§3.2 do briefing).

Um `Protocol`, não uma classe base: o agente recebe algo que sabe `gerar` e não
tem como saber quem atendeu. Isso é o que permite trocar o provedor primário sem
tocar no grafo — e foi exatamente o que salvou o projeto quando o GitHub Models
foi encerrado em 30/07/2026, no meio da execução.

Os tipos aqui são deliberadamente pobres: papel, texto, chamada de tool e uso.
É o mínimo comum entre as APIs no estilo OpenAI e o Ollama, e ficar no mínimo é
o que impede um detalhe de um provedor de vazar para o resto do sistema.

`id_sistema` não é enfeite: é o atributo que a Fase 7 anexa a todo trace e que a
Fase 9 consome para atribuir custo por sistema. Nasce aqui porque depois exigiria
reprocessar trace.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Papel = Literal["system", "user", "assistant", "tool"]


class ErroDeProvedor(RuntimeError):
    """O provedor não entregou resposta utilizável.

    Falha de rede, HTTP de erro ou corpo que não casa com o formato esperado.
    Não é o mesmo que argumento de tool inválido — esse é erro do modelo, e o
    tratamento dele mora em `tools/registro.py`.
    """


@dataclass(frozen=True, slots=True)
class ChamadaDeTool:
    """Uma intenção do modelo de executar uma tool, ainda não validada.

    `argumentos` é o que o modelo mandou, cru. Se ele mandou lixo, o lixo chega
    aqui intacto de propósito: quem julga é o Pydantic da tool, e o erro dessa
    validação é o insumo do laço de autocorreção.
    """

    id: str
    nome: str
    argumentos: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Mensagem:
    """Uma linha da conversa, no mínimo comum entre as APIs."""

    papel: Papel
    conteudo: str = ""
    chamadas: tuple[ChamadaDeTool, ...] = ()
    id_chamada: str | None = None

    @classmethod
    def sistema(cls, conteudo: str) -> Mensagem:
        return cls(papel="system", conteudo=conteudo)

    @classmethod
    def usuario(cls, conteudo: str) -> Mensagem:
        return cls(papel="user", conteudo=conteudo)

    @classmethod
    def assistente(cls, conteudo: str = "", chamadas: Sequence[ChamadaDeTool] = ()) -> Mensagem:
        return cls(papel="assistant", conteudo=conteudo, chamadas=tuple(chamadas))

    @classmethod
    def resultado_de_tool(cls, id_chamada: str, conteudo: str) -> Mensagem:
        """O retorno de uma tool, incluindo o erro estruturado de validação."""
        return cls(papel="tool", conteudo=conteudo, id_chamada=id_chamada)


@dataclass(frozen=True, slots=True)
class EspecificacaoDeTool:
    """Como uma tool se apresenta ao modelo: nome, o que faz e o schema.

    Vive na camada de LLM, e não em `tools/`, para que a dependência aponte numa
    direção só: `tools` conhece o provedor, o provedor não conhece as tools.
    """

    nome: str
    descricao: str
    parametros: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Uso:
    """Tokens da chamada. Zero quando o provedor não informa."""

    tokens_entrada: int = 0
    tokens_saida: int = 0


@dataclass(frozen=True, slots=True)
class RespostaLLM:
    """O que o agente recebe de volta, seja qual for o provedor."""

    conteudo: str = ""
    chamadas: tuple[ChamadaDeTool, ...] = ()
    modelo: str = ""
    id_sistema: str = ""
    uso: Uso = field(default_factory=Uso)
    motivo_parada: str = ""

    @property
    def pediu_tool(self) -> bool:
        return bool(self.chamadas)


@runtime_checkable
class ProvedorLLM(Protocol):
    """Gera uma resposta a partir de mensagens, opcionalmente com tools.

    `id_sistema` identifica a implementação no inventário da Fase 9 — é o `groq`,
    `azure_openai` ou `ollama` que aparece no trace.
    """

    id_sistema: str

    def gerar(
        self,
        mensagens: Sequence[Mensagem],
        *,
        tools: Sequence[EspecificacaoDeTool] = (),
        temperatura: float | None = None,
    ) -> RespostaLLM: ...
