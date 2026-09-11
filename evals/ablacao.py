"""Mede a tabela de ablação da Fase 3 e a grava em `evals/ablacao.md`.

O critério de pronto mais importante do projeto (§6 do briefing) é um número
medido, não uma alegação. Este script roda as quatro configurações — só
vetorial, híbrido com RRF, híbrido com re-ranking, e este último mais o escopo
por norma — sobre as mesmas perguntas do `golden.jsonl` e escreve a tabela com
`recall@5` e `MRR`.

As configurações são argumentos do mesmo `Recuperador`. Implementá-las em
paralelo daria caminhos de código que divergem com o tempo, e aí a comparação
mediria a divergência, não o pipeline. Pelo mesmo motivo, o escopo da quarta
linha é resolvido por `escopo_para`, a função que a tool usa em produção.

Só as perguntas `tipo == "rag"` entram na conta: as de `"nao_sei"` e `"sql"` não
têm artigo esperado, e medir recall contra conjunto vazio não significa nada.
Elas são avaliadas nas camadas 1 e 3 da Fase 7.

Uso:
    .venv/Scripts/python.exe evals/ablacao.py
    .venv/Scripts/python.exe evals/ablacao.py --k 10 --saida evals/ablacao_k10.md
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from copiloto.recuperacao import MODOS, Modo, Recuperador
from copiloto.tools.buscar_normativo import NORMA_INEXISTENTE, escopo_para
from copiloto.tools.schemas import EntradaBuscarNormativo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.metricas import (  # noqa: E402 — a raiz precisa entrar no path antes
    PerguntaDeGabarito,
    caminho_legivel,
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
class Configuracao:
    """Uma linha da tabela: um modo do pipeline, com ou sem escopo por norma.

    O escopo não é um quarto `modo` porque não substitui os outros três — ele se
    soma ao melhor deles. Modelar como flag mantém `Modo` sendo o que sempre foi
    (que combinação de buscas roda) e deixa explícito que a quarta linha é a
    terceira mais uma coisa.
    """

    modo: Modo
    escopo: bool = False

    @property
    def rotulo(self) -> str:
        return ROTULOS[self.modo] + (" + escopo por norma" if self.escopo else "")


CONFIGURACOES: tuple[Configuracao, ...] = (
    *(Configuracao(modo) for modo in MODOS),
    Configuracao("hibrido_rerank", escopo=True),
)


def escopo_da_pergunta(
    recuperador: Recuperador, pergunta: str, *, ativo: bool = True
) -> str | None:
    """A norma que a pergunta nomeia, resolvida contra o catálogo.

    Chama o mesmo `escopo_para` da tool: a linha escopada tem de medir o
    que a produção faz, não uma aproximação dele. Se a detecção mudar e a tabela
    continuar mostrando o ganho antigo, a tabela vira propaganda.

    Não é oráculo: o escopo sai do **texto da pergunta**, nunca do `esperado` do
    gabarito. Um escopo tirado da resposta certa mediria a resposta certa.
    """
    if not ativo:
        return None
    entrada = EntradaBuscarNormativo(pergunta=pergunta)
    achado = escopo_para(entrada, recuperador.catalogo)
    return None if achado is NORMA_INEXISTENTE else achado


@dataclass(frozen=True, slots=True)
class Metricas:
    configuracao: Configuracao
    perguntas: int
    recall: float
    mrr: float
    sem_acerto: tuple[str, ...]

    @property
    def rotulo(self) -> str:
        return self.configuracao.rotulo


def medir(
    recuperador: Recuperador,
    perguntas: Sequence[PerguntaDeGabarito],
    *,
    configuracao: Configuracao,
    k: int,
) -> Metricas:
    """Roda o pipeline em uma configuração e devolve `recall@k` e MRR.

    As contas vêm de `evals/metricas.py`, não daqui: `recall@5` na tabela de
    ablação e `recall@5` no relatório da Fase 7 têm de ser o mesmo número
    calculado do mesmo jeito, ou a comparação entre eles não significa nada.
    """
    postos = [
        posto_do_acerto(
            recuperador.buscar(
                pergunta.pergunta,
                modo=configuracao.modo,
                k_final=k,
                escopo=escopo_da_pergunta(
                    recuperador, pergunta.pergunta, ativo=configuracao.escopo
                ),
            ),
            pergunta.esperado,
        )
        for pergunta in perguntas
    ]
    return Metricas(
        configuracao=configuracao,
        perguntas=len(perguntas),
        recall=recall(postos),
        mrr=mrr(postos),
        sem_acerto=tuple(p.id for p, posto in zip(perguntas, postos, strict=True) if posto is None),
    )


def _git(*argumentos: str) -> str:
    return subprocess.run(
        ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit() -> str:
    """Commit em que a medição rodou. Número sem procedência não vale nada.

    Árvore suja ganha o sufixo `+sujo`, e ele não é enfeite: medição feita sobre
    alterações não commitadas **não é identificada pelo commit**. Anotar só o
    hash convida a comparar duas execuções como se fossem do mesmo código quando
    uma delas rodou sobre outra coisa. Esta tabela existe justamente para
    comparar, então a ressalva vale mais aqui que em qualquer outro lugar.
    """
    try:
        commit = _git("rev-parse", "--short", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "desconhecido"
    try:
        sujo = bool(_git("status", "--porcelain", "--untracked-files=no"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return commit
    return f"{commit}+sujo" if sujo else commit


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
    linhas += [f"| {m.rotulo} | {m.recall:.3f} | {m.mrr:.3f} |" for m in resultados]
    linhas += ["", "## Perguntas sem acerto por configuração", ""]
    for m in resultados:
        falhas = ", ".join(m.sem_acerto) if m.sem_acerto else "nenhuma"
        linhas.append(f"- **{m.rotulo}**: {falhas}")
    linhas += ["", *_leitura(resultados), "", *NOTA_DO_MODELO]
    return "\n".join(linhas)


def _leitura(resultados: Sequence[Metricas]) -> list[str]:
    """A conclusão, derivada dos números — não redigida ao lado deles.

    Escrever "o re-ranking melhorou" à mão criaria a chance de a frase
    sobreviver a uma medição que a desminta. Aqui ela é recalculada junto com a
    tabela, então mentir exigiria mudar o código.
    """
    por_config = {m.configuracao: m for m in resultados}
    denso = por_config[Configuracao("denso")]
    hibrido = por_config[Configuracao("hibrido")]
    rerank = por_config[Configuracao("hibrido_rerank")]
    escopado = por_config[Configuracao("hibrido_rerank", escopo=True)]

    def _frase(nome: str, antes: Metricas, depois: Metricas) -> str:
        delta = depois.recall - antes.recall
        # Delta zero não leva "em 0.000": a frase existe para ser lida, e
        # "não mudou o recall em 0.000" é ruído onde devia haver um fato.
        if delta:
            verbo = "melhorou" if delta > 0 else "piorou"
            movimento = (
                f"{verbo} o recall@5 em {abs(delta):.3f} "
                f"({antes.recall:.3f} para {depois.recall:.3f})"
            )
        else:
            movimento = f"não mudou o recall@5 ({antes.recall:.3f})"
        return f"- {nome} {movimento}; o MRR variou {abs(depois.mrr - antes.mrr):.3f}."

    resistentes = (
        set(denso.sem_acerto)
        & set(hibrido.sem_acerto)
        & set(rerank.sem_acerto)
        & set(escopado.sem_acerto)
    )
    resolvidas = set(rerank.sem_acerto) - set(escopado.sem_acerto)
    quebradas = set(escopado.sem_acerto) - set(rerank.sem_acerto)
    linhas = [
        "## Leitura",
        "",
        _frase("Somar o BM25 ao vetorial", denso, hibrido),
        _frase("Somar o re-ranking ao híbrido", hibrido, rerank),
        _frase("Somar o escopo por norma", rerank, escopado),
        "",
    ]
    if resolvidas:
        linhas.append(
            f"O escopo resolveu {', '.join(sorted(resolvidas))} — perguntas que nomeiam a "
            "norma e antes disputavam com o corpus inteiro."
        )
    if quebradas:
        # Escopo é redução de universo: se ele *perde* uma pergunta, a norma
        # deduzida do texto não é a que contém a resposta. Isso não é ruído a
        # tolerar, é a hipótese da detecção automática sendo desmentida.
        linhas.append(
            f"**O escopo perdeu {', '.join(sorted(quebradas))}**, que a configuração sem "
            "escopo recuperava: nessas perguntas a norma nomeada não é a que responde, e a "
            "dedução automática as prejudica."
        )
    linhas.append("")
    if resistentes:
        linhas += [
            f"Nenhuma configuração recupera {', '.join(sorted(resistentes))}. São o alvo da",
            "próxima rodada: o que falha nas quatro não é problema de ordenação nem de",
            "universo — é o artigo certo não estar entre os candidatos que a busca gerou.",
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
    "**Aquele 0,49 era um caso, não a mediana, e a diferença importa.** Refeito sobre as",
    "dez primeiras perguntas do gabarito, o espalhamento total tem mediana de 0,046 — uma",
    "ordem de grandeza abaixo. E o número que decide o que `k_final` entrega não é o",
    "total: é o espalhamento *dentro do top-6*, que tem mediana de **0,0015**. O modelo",
    "atual satura no topo, na mesma ordem de grandeza pela qual o multilíngue foi",
    "rejeitado. Ele foi escolhido por comparação com um modelo pior, e isso não é o mesmo",
    "que ter sido aprovado.",
    "",
    "**A correção óbvia foi testada e reprovada.** Se os primeiros colocados empatam, a",
    "ordem entre eles seria ruído, e desempatar pelo posto do RRF — que sozinho dá recall",
    "0,812 — deveria recuperar sinal. Medido com epsilon de 0,0005 a 0,05: o recall@5 não",
    "se move em nenhum valor (0,844) e o MRR **piora** (0,694 para 0,626 em 0,005). Score",
    "comprimido não é score sem informação: a ordem que o cross-encoder produz continua",
    "melhor que a do RRF. Quem for reabrir isto, reabra pelo modelo, não pelo desempate.",
    "",
    "## O que o `score_minimo` não decide",
    "",
    "Varrido de 0,0 a 0,7, nenhum valor mudou recall@5 nem MRR. Sobre perguntas que têm",
    "resposta no corpus, o corte é inerte — está em 0,5 por ser o valor do §13, não por",
    "medição que o aprove. O trabalho real dele é descartar quando nada é relevante, e",
    "isso quem exercita são as cinco perguntas de «não sei» do gabarito, avaliadas na",
    "Fase 7.",
    "",
    "**Cuidado com a palavra «inerte».** Ele é inerte para o `recall@5`, e não para o",
    "que chega ao modelo: em g06 o corte reduz a entrega de 19 artigos para 3. Como o",
    "que ele elimina já estava abaixo do 5º lugar, a métrica não se move — mas a",
    "quantidade de contexto que sustenta a resposta, sim. Ver `evals/varredura.md`.",
    "",
    "## Largura de candidatos: medida, e não é o gargalo",
    "",
    "`k_denso` e `k_esparso` foram varridos em 30, 60, 100 e 150 com o escopo ligado, e o",
    "`recall@5` não sobe: oscila em torno de 0,844 trocando quais perguntas erram, a um",
    "custo de recuperação que triplica. A leitura é que o limite não está em quantos",
    "candidatos a busca gera, e sim em o re-ranking saber ordená-los — mais candidatos",
    "dão ao cross-encoder mais chances de pôr a coisa errada no topo. Números e tempos",
    "por configuração em `evals/varredura.md`.",
    "",
]


KS_DA_VARREDURA = (30, 60, 100, 150)
LIMIARES_DA_VARREDURA = (0.0, 0.1, 0.3, 0.5)


def varrer(
    recuperador: Recuperador,
    perguntas: Sequence[PerguntaDeGabarito],
    *,
    k: int,
    saida: Path,
    anunciar: Callable[[str], None],
) -> None:
    """Varre largura de candidatos × `score_minimo` e grava linha a linha.

    Separado da tabela de ablação porque responde outra pergunta. A ablação diz
    *que pipeline* usar; a varredura diz *com que números* — e custa dez vezes
    mais, porque cada largura é um passe de cross-encoder sobre o gabarito
    inteiro. Rodar isso a cada `ablacao.py` tornaria a tabela cara demais para
    ser rodada, que é o jeito mais eficiente de fazer uma medição parar de
    existir.

    Duas economias que mudam o custo e não o resultado:

    * `score_minimo` é um filtro sobre a lista **já reordenada**, então um passe
      por largura serve para todos os limiares. Um passe por par `(k, limiar)`
      refaria o cross-encoder para obter exatamente os mesmos scores.
    * cada linha vai para o disco assim que sai. A primeira tentativa desta
      medição foi interrompida no meio e perdeu tudo, porque a saída estava
      represada num `grep`. Execução longa que só publica no fim é execução que
      não publica.
    """
    linhas = [
        "# Varredura de parâmetros de recuperação",
        "",
        "Gerada por `evals/ablacao.py --varredura`. **Não editar à mão.**",
        "",
        f"- Medido em: {date.today().isoformat()}",
        f"- Commit: `{_commit()}`",
        f"- Configuração: híbrido + re-ranking + escopo por norma, avaliado em k={k}",
        f"- Re-ranking: `{recuperador.parametros.modelo_rerank}`",
        "",
        f"| k candidatos | score_minimo | recall@{k} | MRR | s/pergunta | sem acerto |",
        "|---|---|---|---|---|---|",
    ]
    saida.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    base = recuperador.parametros
    for largura in KS_DA_VARREDURA:
        recuperador._parametros = dataclasses.replace(
            base, k_denso=largura, k_esparso=largura, score_minimo=0.0
        )
        inicio = time.time()
        # Uma recuperação larga por pergunta; os limiares são recortes dela.
        listas = [
            recuperador.buscar(
                p.pergunta,
                modo="hibrido_rerank",
                k_final=TETO_DA_VARREDURA,
                escopo=escopo_da_pergunta(recuperador, p.pergunta),
            )
            for p in perguntas
        ]
        por_pergunta = (time.time() - inicio) / max(len(perguntas), 1)
        for limiar in LIMIARES_DA_VARREDURA:
            postos = [
                posto_do_acerto([t for t in lista if t.score >= limiar][:k], p.esperado)
                for lista, p in zip(listas, perguntas, strict=True)
            ]
            falhas = ", ".join(
                p.id for p, posto in zip(perguntas, postos, strict=True) if posto is None
            )
            linha = (
                f"| {largura} | {limiar:.1f} | {recall(postos):.3f} | {mrr(postos):.3f} "
                f"| {por_pergunta:.1f} | {falhas or 'nenhuma'} |"
            )
            with saida.open("a", encoding="utf-8") as arquivo:
                arquivo.write(linha + "\n")
            anunciar(f"k={largura:<4} s>={limiar:.1f}  recall@{k}={recall(postos):.3f}")
    recuperador._parametros = base

    with saida.open("a", encoding="utf-8") as arquivo:
        arquivo.write("\n" + "\n".join(_leitura_da_varredura()) + "\n")


# Teto da lista recuperada durante a varredura. Alto de propósito: os limiares
# são aplicados sobre esta lista, e um teto baixo cortaria antes do filtro que
# se quer medir.
TETO_DA_VARREDURA = 200


def _leitura_da_varredura() -> list[str]:
    return [
        "## Como ler",
        "",
        "As linhas de mesma largura compartilham o passe de re-ranking: `score_minimo` é",
        "filtro sobre a lista já ordenada, então limiar diferente não refaz a busca.",
        "",
        "Se o `recall` não se move ao longo dos limiares de uma mesma largura, o corte não",
        "está decidindo o top-k — o que ele elimina já estava abaixo dele. Isso **não**",
        "quer dizer que o corte é inofensivo: ele decide quantos artigos chegam ao modelo,",
        "e essa quantidade não aparece nesta tabela.",
        "",
        "Se o `recall` não sobe ao longo das larguras, o gargalo não é quantos candidatos",
        "a busca gera, e sim o re-ranking saber ordená-los.",
        "",
    ]


def main() -> None:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--k", type=int, default=5, help="quantos trechos avaliar (padrão: 5)")
    analisador.add_argument("--saida", type=Path, default=RAIZ / "evals" / "ablacao.md")
    analisador.add_argument(
        "--varredura",
        action="store_true",
        help="em vez da tabela, varre largura de candidatos x score_minimo (lento)",
    )
    analisador.add_argument("--saida-varredura", type=Path, default=RAIZ / "evals" / "varredura.md")
    argumentos = analisador.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    perguntas = carregar_golden(RAIZ / "evals" / "golden.jsonl", tipo="rag")
    recuperador = Recuperador.abrir(RAIZ)

    if argumentos.varredura:
        # `print` e não logging: isto é script de linha de comando, e a saída é
        # o produto. A proibição de `print` vale para o código de produção.
        varrer(
            recuperador,
            perguntas,
            k=argumentos.k,
            saida=argumentos.saida_varredura,
            anunciar=print,
        )
        print(f"varredura gravada em {caminho_legivel(argumentos.saida_varredura)}")
        return

    resultados = []
    for configuracao in CONFIGURACOES:
        metricas = medir(recuperador, perguntas, configuracao=configuracao, k=argumentos.k)
        resultados.append(metricas)
        # `print` e não logging: isto é script de linha de comando, e a saída é
        # o produto. A proibição de `print` vale para o código de produção.
        print(
            f"{metricas.rotulo:40} recall@{argumentos.k}={metricas.recall:.3f} "
            f"MRR={metricas.mrr:.3f}"
        )

    argumentos.saida.write_text(
        montar_relatorio(resultados, recuperador=recuperador, k=argumentos.k), encoding="utf-8"
    )
    print(f"tabela gravada em {caminho_legivel(argumentos.saida)}")


if __name__ == "__main__":
    main()
