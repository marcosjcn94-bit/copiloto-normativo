"""Registro, despacho validado e laço de autocorreção de schema.

É aqui que o contrato vira comportamento. A cadeia é:

    modelo → ChamadaDeTool (crua) → Pydantic → executor tipado → SaidaX
                                        ↓ falhou
                                  ErroDeTool (JSON) → volta ao modelo

**Por que o erro volta estruturado.** Devolver `"erro: entrada inválida"` obriga
o modelo a adivinhar o que consertar, e ele adivinha mal — costuma reescrever o
argumento certo e manter o errado. Devolver campo, valor recebido e valores
permitidos transforma a correção em preenchimento, não em adivinhação. A taxa de
erro de validação de tool é métrica de eval na Fase 7 (`erro_tool_max = 0,02`),
então o formato desta mensagem é parte do sistema, não conveniência de log.

**Por que o laço tem teto.** `max_autocorrecao` vem de `config/parametros.toml`.
Modelo que erra o schema duas vezes seguidas não está a uma tentativa de acertar:
está preso. A terceira falha aborta para o fallback — que é o agente dizer que
não conseguiu, e não inventar uma resposta sem tool.

Este módulo não é uma tool e não tem estado global: as dependências entram por
`montar_registro`, e o provedor de LLM entra por parâmetro.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from copiloto.llm.provedor import (
    ChamadaDeTool,
    EspecificacaoDeTool,
    Mensagem,
    ProvedorLLM,
    RespostaLLM,
)
from copiloto.tools import buscar_normativo as mod_busca
from copiloto.tools import consultar_catalogo as mod_catalogo
from copiloto.tools import registrar_crm as mod_crm
from copiloto.tools.buscar_normativo import FonteDeTrechos
from copiloto.tools.schemas import (
    EntradaBuscarNormativo,
    EntradaConsultarCatalogo,
    EntradaRegistrarCrm,
)

logger = logging.getLogger(__name__)

TipoDeErro = Literal["tool_desconhecida", "schema_invalido", "falha_de_execucao"]


@dataclass(frozen=True, slots=True)
class ParametrosDeTools:
    """A seção `[tools]` do TOML mais o teto de autocorreção, que é de `[grafo]`."""

    limite_catalogo: int
    fator_sobrebusca: int
    timeout_crm: float
    max_autocorrecao: int


def carregar_parametros(caminho: Path) -> ParametrosDeTools:
    """Lê `config/parametros.toml`. Chave a mais ou a menos falha aqui."""
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    return ParametrosDeTools(
        **dados["tools"],
        max_autocorrecao=dados["grafo"]["max_autocorrecao"],
    )


@dataclass(frozen=True, slots=True)
class Problema:
    """Um campo reprovado, no vocabulário do modelo e não no do Pydantic."""

    campo: str
    problema: str
    recebido: Any = None
    permitidos: str = ""

    def para_dicionario(self) -> dict[str, Any]:
        corpo: dict[str, Any] = {"campo": self.campo, "problema": self.problema}
        if self.recebido is not None:
            corpo["recebido"] = self.recebido
        if self.permitidos:
            corpo["permitidos"] = self.permitidos
        return corpo


@dataclass(frozen=True, slots=True)
class ErroDeTool:
    """O que volta ao modelo quando a chamada não passa. Sempre acionável."""

    tool: str
    tipo: TipoDeErro
    mensagem: str
    problemas: tuple[Problema, ...] = ()

    def para_modelo(self) -> str:
        """JSON compacto: o modelo lê melhor chave-valor que prosa de exceção."""
        return json.dumps(
            {
                "erro": self.tipo,
                "tool": self.tool,
                "mensagem": self.mensagem,
                "problemas": [p.para_dicionario() for p in self.problemas],
                "como_corrigir": (
                    "Chame a tool outra vez corrigindo apenas os campos listados. "
                    "Não invente campos: argumento fora do schema é recusado."
                ),
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class ResultadoDeTool:
    """Saída tipada ou erro estruturado — nunca as duas, nunca nenhuma."""

    tool: str
    saida: BaseModel | None = None
    erro: ErroDeTool | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None

    def para_modelo(self) -> str:
        if self.erro is not None:
            return self.erro.para_modelo()
        assert self.saida is not None
        return self.saida.model_dump_json()


@dataclass(frozen=True, slots=True)
class ToolRegistrada:
    """Uma tool pronta para o modelo: contrato, descrição e executor ligado."""

    nome: str
    descricao: str
    entrada: type[BaseModel]
    executar: Callable[[Any], BaseModel]
    escrita: bool = False

    def especificacao(self) -> EspecificacaoDeTool:
        return EspecificacaoDeTool(
            nome=self.nome,
            descricao=self.descricao,
            parametros=self.entrada.model_json_schema(),
        )


def montar_registro(
    *,
    recuperador: FonteDeTrechos,
    catalogo: sqlite3.Connection,
    parametros: ParametrosDeTools,
    thread_id: str,
    passo_atual: Callable[[], int] = lambda: 1,
) -> dict[str, ToolRegistrada]:
    """Liga as dependências às três tools e devolve o registro por nome.

    `registrar_crm` é registrada com `propor_registro`, não com o envio: o que o
    agente pode executar sozinho é a proposta. A escrita de verdade
    (`enviar_registro`) fica fora do registro justamente para que não exista
    caminho de código em que o modelo a alcance sem passar pelo humano.

    `passo_atual` é função, e não número, porque o passo muda a cada volta do
    grafo e é ingrediente da chave de idempotência.
    """
    return {
        mod_busca.NOME: ToolRegistrada(
            nome=mod_busca.NOME,
            descricao=mod_busca.DESCRICAO,
            entrada=EntradaBuscarNormativo,
            executar=lambda entrada: mod_busca.buscar_normativo(
                entrada,
                recuperador=recuperador,
                catalogo=catalogo,
                fator_sobrebusca=parametros.fator_sobrebusca,
            ),
        ),
        mod_catalogo.NOME: ToolRegistrada(
            nome=mod_catalogo.NOME,
            descricao=mod_catalogo.DESCRICAO,
            entrada=EntradaConsultarCatalogo,
            executar=lambda entrada: mod_catalogo.consultar_catalogo(
                entrada,
                catalogo=catalogo,
                limite_padrao=parametros.limite_catalogo,
            ),
        ),
        mod_crm.NOME: ToolRegistrada(
            nome=mod_crm.NOME,
            descricao=mod_crm.DESCRICAO,
            entrada=EntradaRegistrarCrm,
            executar=lambda entrada: mod_crm.propor_registro(
                entrada,
                thread_id=thread_id,
                passo=passo_atual(),
            ),
            escrita=True,
        ),
    }


def especificacoes(registro: Mapping[str, ToolRegistrada]) -> list[EspecificacaoDeTool]:
    """O bloco `tools` que vai ao provedor, em ordem estável."""
    return [registro[nome].especificacao() for nome in sorted(registro)]


def despachar(chamada: ChamadaDeTool, registro: Mapping[str, ToolRegistrada]) -> ResultadoDeTool:
    """Valida os argumentos crus e executa. Nunca levanta por culpa do modelo."""
    tool = registro.get(chamada.nome)
    if tool is None:
        return ResultadoDeTool(
            tool=chamada.nome,
            erro=ErroDeTool(
                tool=chamada.nome,
                tipo="tool_desconhecida",
                mensagem=f"não existe tool chamada {chamada.nome!r}",
                problemas=(
                    Problema(
                        campo="nome_da_tool",
                        problema="tool inexistente",
                        recebido=chamada.nome,
                        permitidos=", ".join(sorted(registro)),
                    ),
                ),
            ),
        )

    try:
        entrada = tool.entrada.model_validate(chamada.argumentos)
    except ValidationError as erro:
        logger.info("schema recusado", extra={"tool": tool.nome, "erros": erro.error_count()})
        return ResultadoDeTool(
            tool=tool.nome,
            erro=ErroDeTool(
                tool=tool.nome,
                tipo="schema_invalido",
                mensagem="os argumentos não passam no contrato da tool",
                problemas=tuple(_problemas(erro)),
            ),
        )

    try:
        saida = tool.executar(entrada)
    except Exception as erro:  # noqa: BLE001 - a falha vira erro tipado, não sobe crua
        logger.exception("tool falhou na execução", extra={"tool": tool.nome})
        return ResultadoDeTool(
            tool=tool.nome,
            erro=ErroDeTool(
                tool=tool.nome,
                tipo="falha_de_execucao",
                mensagem=f"{type(erro).__name__}: {erro}",
            ),
        )
    return ResultadoDeTool(tool=tool.nome, saida=saida)


def _problemas(erro: ValidationError) -> list[Problema]:
    """Traduz `ValidationError` para algo que o modelo consiga agir em cima."""
    problemas = []
    for detalhe in erro.errors():
        contexto = detalhe.get("ctx") or {}
        problemas.append(
            Problema(
                campo=".".join(str(parte) for parte in detalhe["loc"]) or "(raiz)",
                problema=detalhe.get("msg", detalhe["type"]),
                recebido=_recebido(detalhe.get("input")),
                permitidos=str(contexto.get("expected", "")),
            )
        )
    return problemas


def _recebido(valor: Any) -> Any:
    """Ecoa o valor recusado sem devolver um payload inteiro para o contexto."""
    if isinstance(valor, str):
        return valor if len(valor) <= 120 else valor[:117] + "..."
    if isinstance(valor, dict | list | tuple):
        return f"<{type(valor).__name__} com {len(valor)} itens>"
    return valor


@dataclass(frozen=True, slots=True)
class CicloDeAutocorrecao:
    """O que o grafo da Fase 5 recebe: transcrição, resultados e se abortou."""

    mensagens: tuple[Mensagem, ...] = ()
    resultados: tuple[ResultadoDeTool, ...] = ()
    resposta: RespostaLLM | None = None
    tentativas: int = 0
    abortado: bool = False
    motivo: str = ""

    @property
    def concluiu(self) -> bool:
        return not self.abortado and all(r.ok for r in self.resultados)


def executar_com_autocorrecao(
    provedor: ProvedorLLM,
    mensagens: Sequence[Mensagem],
    *,
    registro: Mapping[str, ToolRegistrada],
    max_autocorrecao: int,
    temperatura: float | None = None,
) -> CicloDeAutocorrecao:
    """Pede tools ao modelo, valida e devolve o erro para ele corrigir.

    Uma volta é: gerar → despachar → se alguma chamada reprovou no schema,
    devolver o erro estruturado e gerar de novo. `max_autocorrecao` limita quantas
    correções são concedidas; esgotadas, o ciclo aborta e quem decide o que fazer
    é o grafo, que responde o fallback em vez de seguir sem base.

    Erro de execução (`falha_de_execucao`) não gera correção: argumento válido que
    quebrou na execução é problema do sistema, não do modelo — reformular o
    argumento não conserta e só queima tentativa.
    """
    if max_autocorrecao < 0:
        raise ValueError("max_autocorrecao precisa ser >= 0")

    transcricao = list(mensagens)
    specs = especificacoes(registro)
    resposta: RespostaLLM | None = None

    for tentativa in range(1, max_autocorrecao + 2):
        resposta = provedor.gerar(transcricao, tools=specs, temperatura=temperatura)

        if not resposta.pediu_tool:
            return CicloDeAutocorrecao(
                mensagens=tuple(transcricao),
                resposta=resposta,
                tentativas=tentativa,
                motivo="modelo respondeu sem pedir tool",
            )

        transcricao.append(Mensagem.assistente(resposta.conteudo, resposta.chamadas))
        resultados = [despachar(chamada, registro) for chamada in resposta.chamadas]
        for chamada, resultado in zip(resposta.chamadas, resultados, strict=True):
            transcricao.append(Mensagem.resultado_de_tool(chamada.id, resultado.para_modelo()))

        corrigiveis = [r for r in resultados if r.erro and r.erro.tipo != "falha_de_execucao"]
        if not corrigiveis:
            return CicloDeAutocorrecao(
                mensagens=tuple(transcricao),
                resultados=tuple(resultados),
                resposta=resposta,
                tentativas=tentativa,
                abortado=any(not r.ok for r in resultados),
                motivo="tool falhou na execução" if any(not r.ok for r in resultados) else "",
            )

        logger.warning(
            "autocorreção de schema",
            extra={"tentativa": tentativa, "reprovadas": len(corrigiveis)},
        )

    return CicloDeAutocorrecao(
        mensagens=tuple(transcricao),
        resposta=resposta,
        tentativas=max_autocorrecao + 1,
        abortado=True,
        motivo=(
            f"schema inválido em {max_autocorrecao + 1} tentativas seguidas; abortado para fallback"
        ),
    )


def cliente_de_crm(base_url: str, timeout: float) -> httpx.Client:
    """Cliente HTTP do CRM falso (Fase 6). Fica aqui para não nascer solto."""
    return httpx.Client(base_url=base_url, timeout=timeout)
