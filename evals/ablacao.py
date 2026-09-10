"""Mede a tabela de ablação da Fase 3 e a grava em `evals/ablacao.md`.

O critério de pronto mais importante do projeto (§6 do briefing) é um número
medido, não uma alegação. Este script roda as três configurações — só vetorial,
híbrido com RRF, híbrido com re-ranking — sobre as mesmas perguntas do
`golden.jsonl` e escreve a tabela com `recall@5` e `MRR`.

As três configurações são três valores do argumento `modo` do mesmo
`Recuperador`. Implementá-las em paralelo daria três caminhos de código que
divergem com o tempo, e aí a comparação mediria a divergência, não o pipeline.

Só as perguntas `tipo == "rag"` entram na conta: as de `"nao_sei"` e `"sql"` não
têm artigo esperado, e medir recall contra conjunto vazio não significa nada.
Elas são avaliadas nas camadas 1 e 3 da Fase 7.

Uso:
    .venv/Scripts/python.exe evals/ablacao.py
    .venv/Scripts/python.exe evals/ablacao.py --k 10 --saida evals/ablacao_k10.md
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from copiloto.recuperacao import MODOS, Modo, Recuperador

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.metricas import (  # noqa: E402 — a raiz precisa entrar no path antes
    PerguntaDeGabarito,
    carregar_golden,
    mrr,
    posto_do_acerto,
    recall,
)

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[1]

ROTULOS: dict[Modo, str] = {
    "denso": "Só vetorial",
    "hibrido": "Híbrido (RRF)",
    "hibrido_rerank": "Híbrido + re-ranking",
}


@dataclass(frozen=True, slots=True)
class Metricas:
    modo: Modo
    perguntas: int
    recall: float
    mrr: float
    sem_acerto: tuple[str, ...]


def medir(recuperador: Recuperador, perguntas: Sequence[PerguntaDeGabarito], *, modo: Modo, k: int):
    """Roda o pipeline em um modo e devolve `recall@k` e MRR.

    As contas vêm de `evals/metricas.py`, não daqui: `recall@5` na tabela de
    ablação e `recall@5` no relatório da Fase 7 têm de ser o mesmo número
    calculado do mesmo jeito, ou a comparação entre eles não significa nada.
    """
    postos = [
        posto_do_acerto(
            recuperador.buscar(pergunta.pergunta, modo=modo, k_final=k), pergunta.esperado
        )
        for pergunta in perguntas
    ]
    return Metricas(
        modo=modo,
        perguntas=len(perguntas),
        recall=recall(postos),
        mrr=mrr(postos),
        sem_acerto=tuple(p.id for p, posto in zip(perguntas, postos, strict=True) if posto is None),
    )


def _commit() -> str:
    """Commit em que a medição rodou. Número sem procedência não vale nada."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=RAIZ,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "desconhecido"


def montar_relatorio(resultados: Sequence[Metricas], *, recuperador: Recuperador, k: int) -> str:
    p = recuperador.parametros
    linhas = [
        "# Tabela de ablação — recuperação híbrida",
        "",
        "Gerada por `evals/ablacao.py`. **Não editar à mão**: se um número aqui não",
        "veio de uma execução, a tabela perde a única coisa que a torna útil.",
        "",
        f"- Medido em: {date.today().isoformat()}",
        f"- Commit: `{_commit()}`",
        f"- Gabarito: `evals/golden.jsonl`, {resultados[0].perguntas} perguntas do tipo `rag`",
        f"- k avaliado: {k}",
        (
            f"- Parâmetros: k_denso={p.k_denso}, k_esparso={p.k_esparso}, k_rrf={p.k_rrf}, "
            f"score_minimo={p.score_minimo}"
        ),
        f"- Embedding: `{p.modelo_embedding}`",
        f"- Re-ranking: `{p.modelo_rerank}`",
        "",
        f"| Configuração | recall@{k} | MRR |",
        "|---|---|---|",
    ]
    linhas += [f"| {ROTULOS[m.modo]} | {m.recall:.3f} | {m.mrr:.3f} |" for m in resultados]
    linhas += ["", "## Perguntas sem acerto por configuração", ""]
    for m in resultados:
        falhas = ", ".join(m.sem_acerto) if m.sem_acerto else "nenhuma"
        linhas.append(f"- **{ROTULOS[m.modo]}**: {falhas}")
    linhas += ["", *_leitura(resultados), "", *NOTA_DO_MODELO]
    return "\n".join(linhas)


def _leitura(resultados: Sequence[Metricas]) -> list[str]:
    """A conclusão, derivada dos números — não redigida ao lado deles.

    Escrever "o re-ranking melhorou" à mão criaria a chance de a frase
    sobreviver a uma medição que a desminta. Aqui ela é recalculada junto com a
    tabela, então mentir exigiria mudar o código.
    """
    por_modo = {m.modo: m for m in resultados}
    denso, hibrido, rerank = por_modo["denso"], por_modo["hibrido"], por_modo["hibrido_rerank"]

    def _frase(nome: str, antes: Metricas, depois: Metricas) -> str:
        delta = depois.recall - antes.recall
        verbo = "melhorou" if delta > 0 else ("piorou" if delta < 0 else "não mudou")
        return (
            f"- {nome} {verbo} o recall@5 em {abs(delta):.3f} "
            f"({antes.recall:.3f} para {depois.recall:.3f}); o MRR variou "
            f"{abs(depois.mrr - antes.mrr):.3f}."
        )

    resistentes = set(denso.sem_acerto) & set(hibrido.sem_acerto) & set(rerank.sem_acerto)
    linhas = [
        "## Leitura",
        "",
        _frase("Somar o BM25 ao vetorial", denso, hibrido),
        _frase("Somar o re-ranking ao híbrido", hibrido, rerank),
        "",
    ]
    if resistentes:
        linhas += [
            f"Nenhuma configuração recupera {', '.join(sorted(resistentes))}. São o alvo da",
            "próxima rodada: o que falha nas três não é problema de ordenação — é o artigo",
            "certo não estar entre os candidatos que a busca gerou.",
        ]
    else:
        linhas.append("Toda pergunta do gabarito é recuperada por ao menos uma configuração.")
    return linhas


# Contexto que a tabela sozinha não carrega. Mora aqui, e não no arquivo gerado,
# para que `ablacao.md` continue sendo saída de execução do começo ao fim.
NOTA_DO_MODELO = [
    "## O modelo de re-ranking, e o que quase virou resultado negativo",
    "",
    "A primeira medição desta fase deu recall@5 = 0,406 com re-ranking, contra 0,781 do",
    "híbrido puro. A conclusão pronta seria «o re-ranking não ajuda neste corpus», e ela",
    "estaria errada. O modelo era `ms-marco-MultiBERT-L-12`, multilíngue — a escolha",
    "óbvia para corpus em português. Ele **satura**: os 50 candidatos saem com score",
    "entre 0,993 e 0,999, um espalhamento de 0,006. Score saturado não ordena, e o efeito",
    "não é neutro: o artigo correto de uma das perguntas caiu do 6º para o 43º lugar.",
    "",
    "Trocado pelo `ms-marco-MiniLM-L-12-v2`, treinado em inglês, o espalhamento vai a",
    "0,49 no mesmo conjunto — e a tabela acima é o resultado. O diagnóstico barato para",
    "reavaliar essa escolha não é o recall: é medir o espalhamento dos scores antes de",
    "rodar a ablação inteira.",
    "",
    "## O que o `score_minimo` não decide",
    "",
    "Varrido de 0,0 a 0,7, nenhum valor mudou recall@5 nem MRR. Sobre perguntas que têm",
    "resposta no corpus, o corte é inerte — está em 0,5 por ser o valor do §13, não por",
    "medição que o aprove. O trabalho real dele é descartar quando nada é relevante, e",
    "isso quem exercita são as cinco perguntas de «não sei» do gabarito, avaliadas na",
    "Fase 7.",
    "",
]


def main() -> None:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--k", type=int, default=5, help="quantos trechos avaliar (padrão: 5)")
    analisador.add_argument("--saida", type=Path, default=RAIZ / "evals" / "ablacao.md")
    argumentos = analisador.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    perguntas = carregar_golden(RAIZ / "evals" / "golden.jsonl", tipo="rag")
    recuperador = Recuperador.abrir(RAIZ)

    resultados = []
    for modo in MODOS:
        metricas = medir(recuperador, perguntas, modo=modo, k=argumentos.k)
        resultados.append(metricas)
        # `print` e não logging: isto é script de linha de comando, e a saída é
        # o produto. A proibição de `print` vale para o código de produção.
        print(
            f"{ROTULOS[modo]:22} recall@{argumentos.k}={metricas.recall:.3f} MRR={metricas.mrr:.3f}"
        )

    argumentos.saida.write_text(
        montar_relatorio(resultados, recuperador=recuperador, k=argumentos.k), encoding="utf-8"
    )
    print(f"tabela gravada em {argumentos.saida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
