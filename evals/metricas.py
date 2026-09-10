"""As métricas dos evals: `recall@k`, MRR, faithfulness e taxa de erro de tool.

Funções puras sobre dados já colhidos. Quem roda o pipeline é `rodar.py`; quem
roda a ablação é `ablacao.py`. Separar assim tem uma razão só, e ela é o ponto do
§9: **a régua não pode morar junto de quem é medido**. Se cada script calculasse
o próprio recall, dois números com o mesmo nome no README poderiam vir de duas
contas diferentes — e ninguém perceberia.

**Faithfulness aqui não é média de nota de LLM.** É a fração de respostas em que
*toda* citação saiu dos trechos recuperados naquela execução, conferida pelo
mesmo `validar_resposta` que o grafo usa em produção. O juiz da camada 3 mede
outra coisa (se a afirmação em linguagem natural é sustentada pelo trecho), e as
duas entram no relatório com nomes diferentes de propósito: `faithfulness_citacao`
é determinística, `faithfulness_juiz` é opinião de modelo. Fundir as duas num
número só seria esconder qual delas está sustentando a alegação.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

TipoDePergunta = Literal["rag", "nao_sei", "sql"]


@dataclass(frozen=True, slots=True)
class PerguntaDeGabarito:
    """Uma linha do `golden.jsonl`, já tipada.

    `esperado` é conjunto porque há perguntas cuja resposta correta aparece em
    mais de uma norma do corpus — quatro normas tratam do mesmo tema para
    destinatários diferentes. Exigir uma delas em particular mediria sorte.
    """

    id: str
    tipo: TipoDePergunta
    pergunta: str
    grupo: str = ""
    nota: str = ""
    esperado: frozenset[tuple[str, int]] = frozenset()


def carregar_golden(
    caminho: Path, *, tipo: TipoDePergunta | None = None
) -> list[PerguntaDeGabarito]:
    """Lê o gabarito inteiro. `tipo` filtra; sem ele vêm as 40 perguntas."""
    perguntas: list[PerguntaDeGabarito] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        registro = json.loads(linha)
        if tipo is not None and registro["tipo"] != tipo:
            continue
        perguntas.append(
            PerguntaDeGabarito(
                id=registro["id"],
                tipo=registro["tipo"],
                pergunta=registro["pergunta"],
                grupo=registro.get("grupo", ""),
                nota=registro.get("nota", ""),
                esperado=frozenset(
                    (e["id_norma"], int(e["numero_artigo"])) for e in registro.get("esperado", ())
                ),
            )
        )
    return perguntas


# --- recuperação -------------------------------------------------------------


class TrechoRecuperado(Protocol):
    """O mínimo que as métricas de recuperação usam de um trecho.

    Propriedades só de leitura, e não atributos: assim um `Trecho` congelado do
    retriever e um `TrechoCitado` do Pydantic satisfazem o mesmo contrato sem que
    nenhum dos dois precise abrir mão da imutabilidade.
    """

    @property
    def id_norma(self) -> str: ...

    @property
    def numero_artigo(self) -> int: ...


def posto_do_acerto(
    trechos: Sequence[TrechoRecuperado], esperado: frozenset[tuple[str, int]]
) -> int | None:
    """Posição (1-based) do primeiro trecho correto, ou `None` se não veio nenhum."""
    for posto, trecho in enumerate(trechos, start=1):
        if (trecho.id_norma, trecho.numero_artigo) in esperado:
            return posto
    return None


def recall(postos: Iterable[int | None]) -> float:
    """Fração de perguntas com ao menos um trecho correto entre os avaliados.

    O `k` não é parâmetro: ele já foi aplicado por quem cortou a lista de
    trechos. Passar `k` aqui de novo permitiria medir `recall@5` sobre 10
    trechos sem que a chamada denunciasse o erro.
    """
    postos = list(postos)
    if not postos:
        return 0.0
    return sum(1 for p in postos if p is not None) / len(postos)


def mrr(postos: Iterable[int | None]) -> float:
    """Média dos recíprocos do posto. Pergunta sem acerto contribui com zero."""
    postos = list(postos)
    if not postos:
        return 0.0
    return sum(1.0 / p for p in postos if p is not None) / len(postos)


# --- comportamento do agente -------------------------------------------------


def taxa(numerador: int, denominador: int) -> float:
    """Divisão que não explode em denominador zero. Sem amostra, a taxa é zero."""
    return numerador / denominador if denominador else 0.0


def taxa_de_erro_de_tool(chamadas: int, recusadas: int) -> float:
    """Chamadas de tool recusadas no contrato, sobre o total de chamadas.

    É o número do critério de pronto da Fase 7 (`erro_tool_max`). Mede o modelo,
    não o schema: o denominador é toda chamada que o modelo emitiu, e o numerador
    são as que o Pydantic recusou antes de executar. Contar a autocorreção
    bem-sucedida como acerto esconderia exatamente o que se quer ver.
    """
    return taxa(recusadas, chamadas)


@dataclass(frozen=True, slots=True)
class Nota:
    """Uma nota de juiz, com o porquê junto. Nota sem justificativa não é revisável."""

    id_pergunta: str
    criterio: str
    valor: float
    justificativa: str = ""

    @property
    def aprovada(self) -> bool:
        return self.valor >= 1.0


def media(notas: Sequence[Nota]) -> float:
    """Média das notas. Lista vazia é zero, e o relatório diz que a amostra é zero."""
    return sum(n.valor for n in notas) / len(notas) if notas else 0.0


def por_criterio(notas: Sequence[Nota]) -> dict[str, float]:
    """Média separada por critério — é assim que ela entra em `resultados.json`."""
    criterios: dict[str, list[Nota]] = {}
    for nota in notas:
        criterios.setdefault(nota.criterio, []).append(nota)
    return {criterio: media(lista) for criterio, lista in sorted(criterios.items())}


def contra_threshold(valor: float, minimo: float) -> dict[str, Any]:
    """O par medido/exigido, com o veredito calculado — nunca digitado ao lado."""
    return {"valor": round(valor, 4), "minimo": minimo, "aprovado": valor >= minimo}


def contra_teto(valor: float, maximo: float) -> dict[str, Any]:
    """O mesmo, para métrica em que menos é melhor (taxa de erro)."""
    return {"valor": round(valor, 4), "maximo": maximo, "aprovado": valor <= maximo}
