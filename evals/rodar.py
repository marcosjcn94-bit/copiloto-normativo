"""As três camadas de avaliação do §9, e o veredito contra os thresholds do §13.

    .venv/Scripts/python.exe evals/rodar.py --camada 1        # determinística
    .venv/Scripts/python.exe evals/rodar.py --camada 1 2      # + recuperação
    .venv/Scripts/python.exe evals/rodar.py --todas           # + juiz (custa chamada)

| Camada | O que mede                                            | Custa LLM |
|--------|-------------------------------------------------------|-----------|
| 1      | Contrato de tool, orçamento de tokens, citação no ctx  | não       |
| 2      | `recall@5` e MRR contra `golden.jsonl`                 | não       |
| 3      | Faithfulness, relevância, escolha de tool              | sim       |

**Saída em dois arquivos, e isso não é redundância.** `resultados.json` é o que a
Fase 9 lê para montar o painel — o §16.5 exige que nenhum número do painel seja
digitado à mão, então ele precisa de uma fonte legível por máquina.
`relatorio.md` é para gente. Os dois saem da mesma execução; nenhum é escrito
sem a outra metade.

**O que este script faz quando falta corpus.** Ele *não* inventa. Toda verificação
declara se precisa de `/data` e `/indices` — que são gitignored — e, na ausência
deles, aparece no relatório como pulada, com o motivo. É por isso que o CI roda
a camada 1 a cada push sem baixar 130 MB de ONNX: as verificações de contrato e
de orçamento não dependem de corpus nenhum, e as que dependem dizem que não
rodaram em vez de fingir que passaram.

**Divergência conhecida com o briefing.** A tabela do §9 diz que a camada 2 roda
no CI; o texto da Fase 7 diz que o CI roda *a camada 1*. Vale a Fase 7, e a razão
é material: o índice denso é gitignored e reconstruí-lo no runner exigiria rede
para o BCB e download do modelo de embeddings a cada push. O `ablacao.md` guarda
os números da camada 2, medidos na máquina, com o commit anotado.

**Sobre chamar um LLM de juiz.** A camada 3 é um sistema de IA em produção com
fornecedor, custo e modo de falha próprios — é o quarto item do inventário do
§16.1, e é o que mais escapa de inventário real. Por isso ele tem `id_sistema`
próprio (`eval-judge/camada-3`) e não se disfarça de "métrica".
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from copiloto.grafo.grafo import Dependencias, compilar
from copiloto.grafo.guardrails import SEM_BASE_NORMATIVA, validar_resposta
from copiloto.grafo.nos import PROMPT_SISTEMA
from copiloto.grafo.nos import carregar_parametros as parametros_do_grafo
from copiloto.llm.provedor import ErroDeProvedor, Mensagem, ProvedorLLM
from copiloto.observabilidade.tracing import versao_da_ficha
from copiloto.tools.buscar_normativo import NOME as TOOL_BUSCAR
from copiloto.tools.consultar_catalogo import NOME as TOOL_CATALOGO
from copiloto.tools.registro import ParametrosDeTools, especificacoes, montar_registro
from copiloto.tools.registro import carregar_parametros as parametros_de_tools
from copiloto.tools.schemas import (
    EntradaBuscarNormativo,
    EntradaConsultarCatalogo,
    EntradaRegistrarCrm,
    SaidaBuscarNormativo,
    SaidaConsultarCatalogo,
    TrechoCitado,
)

# Rodando como script (`python evals/rodar.py`), a raiz do repositório não está
# no path: o `pip install -e .` publica só `src/`. Sem esta linha, `evals` não
# resolveria como pacote e `metricas` seria importado por um segundo caminho,
# criando duas cópias das mesmas classes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.metricas import (  # noqa: E402 — a raiz precisa entrar no path antes
    Nota,
    PerguntaDeGabarito,
    carregar_golden,
    contra_teto,
    contra_threshold,
    media,
    mrr,
    por_criterio,
    posto_do_acerto,
    recall,
    taxa,
    taxa_de_erro_de_tool,
)

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[1]
GOLDEN = RAIZ / "evals" / "golden.jsonl"
TOML = RAIZ / "config" / "parametros.toml"
CATALOGO = RAIZ / "data" / "catalogo.sqlite"

ID_SISTEMA_JUIZ = "eval-judge/camada-3"

# Estimador de tokens por caracteres. **É estimativa e o relatório diz isso.**
# O tokenizador real do Llama 3.3 não está no ambiente e trazê-lo custaria
# dependência nova para medir uma folga que hoje é de ordem de grandeza. Quatro
# caracteres por token é a razão usual em português para tokenizadores BPE;
# quando a folga deixar de ser confortável, esta constante vira o próximo item a
# verificar — não o número a ajustar.
CHARS_POR_TOKEN = 4


def tokens_aprox(texto: str) -> int:
    return math.ceil(len(texto) / CHARS_POR_TOKEN)


# --- estrutura do resultado --------------------------------------------------


@dataclass(slots=True)
class Resultado:
    """O que uma verificação apurou. `falhas` carrega o id de quem reprovou."""

    id: str
    camada: int
    descricao: str
    total: int = 0
    falhas: tuple[str, ...] = ()
    detalhe: dict[str, Any] = field(default_factory=dict)
    executada: bool = True
    motivo: str = ""

    @property
    def aprovada(self) -> bool:
        """Verificação pulada não reprova — mas também não aprova nada."""
        return not self.falhas if self.executada else True

    def como_dicionario(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "camada": self.camada,
            "descricao": self.descricao,
            "executada": self.executada,
            "motivo": self.motivo,
            "total": self.total,
            "falhas": list(self.falhas),
            "aprovada": self.aprovada,
            "detalhe": self.detalhe,
        }


def pulada(id_: str, camada: int, descricao: str, motivo: str) -> Resultado:
    return Resultado(id=id_, camada=camada, descricao=descricao, executada=False, motivo=motivo)


# --- a sessão que conta em vez de rastrear -----------------------------------


class _ObservacaoContadora:
    """Recebe o mesmo `output` que o span do Langfuse receberia."""

    __slots__ = ("registro", "saida")

    def __init__(self, registro: dict[str, Any]) -> None:
        self.registro = registro
        self.saida: str = ""

    def atualizar(self, **campos: Any) -> None:
        saida = campos.get("output")
        if isinstance(saida, dict):
            self.registro.update(saida)
        elif isinstance(saida, str):
            self.saida = saida


@dataclass(slots=True)
class SessaoContadora:
    """Implementa `Sessao` e conta chamadas de tool em vez de exportar trace.

    **Por que reusar a instrumentação e não ler a transcrição.** O número que a
    Fase 7 precisa — chamadas de tool emitidas e quantas o contrato recusou — já
    passa pelo `deliberar.ciclo` que o trace observa. Recontá-lo garimpando o
    histórico de mensagens criaria uma segunda definição da mesma métrica, que é
    exatamente o que `metricas.py` existe para impedir. De quebra, o eval exercita
    a superfície de observabilidade: se ela quebrar, isto para de contar.
    """

    chamadas: list[str] = field(default_factory=list)
    erros: list[dict[str, str]] = field(default_factory=list)
    evidencias: list[tuple[str, str]] = field(default_factory=list)

    @contextmanager
    def no(self, nome: str, *, tipo: str = "span"):
        registro: dict[str, Any] = {}
        observacao = _ObservacaoContadora(registro)
        yield observacao
        if nome == "deliberar.ciclo":
            self.chamadas.extend(registro.get("tools_chamadas", ()))
            self.erros.extend(registro.get("erros_de_tool", ()))
        elif tipo in ("tool", "retriever") and observacao.saida:
            # O que a tool devolveu, cru. É a evidência que o agente teve em mãos
            # — e é o que o juiz precisa ver para julgar fidelidade sem adivinhar.
            self.evidencias.append((nome, observacao.saida))

    @contextmanager
    def geracao(self, *, nome: str, modelo: str, id_sistema: str):
        yield _ObservacaoContadora({})

    def registrar(self, **campos: Any) -> None:
        return None

    def id_do_trace(self) -> str:
        return ""


# --- camada 1: determinística ------------------------------------------------

# Cada linha é (rótulo, argumentos, tem_de_passar). As mutações não são
# aleatórias: cobrem os quatro modos de erro que o §7 exige que o contrato pegue
# — campo a mais, tipo errado, faixa estourada e literal fora do domínio.
# Uma proposta de registro bem formada. As mutações do CRM saem daqui para que
# cada uma reprove pelo motivo que quer testar, e não por campo obrigatório
# faltando — mutação que falha pelo motivo errado não prova nada.
CRM_VALIDO: dict[str, Any] = {
    "id_cliente": "CLI-0001",
    "assunto": "Prazo de comunicação de incidente cibernético",
    "resumo": ("Cliente perguntou sobre o prazo de comunicação de incidente relevante ao BCB."),
    "normas_citadas": ["Resolução BCB nº 85, de 2021, art. 7º"],
}

CASOS_DE_CONTRATO: tuple[tuple[str, type, dict[str, Any], bool], ...] = (
    (
        "buscar.valido",
        EntradaBuscarNormativo,
        {"pergunta": "Qual o prazo de comunicação de incidente relevante?"},
        True,
    ),
    (
        "buscar.campo_extra",
        EntradaBuscarNormativo,
        {"pergunta": "Qual o prazo de comunicação de incidente?", "top_k": 5},
        False,
    ),
    (
        "buscar.k_acima_do_teto",
        EntradaBuscarNormativo,
        {"pergunta": "Qual o prazo de comunicação de incidente?", "k": 99},
        False,
    ),
    (
        "buscar.tema_inexistente",
        EntradaBuscarNormativo,
        {"pergunta": "Qual o prazo de comunicação de incidente?", "tema": "criptomoedas"},
        False,
    ),
    (
        "buscar.pergunta_curta_demais",
        EntradaBuscarNormativo,
        {"pergunta": "85"},
        False,
    ),
    (
        "catalogo.valido",
        EntradaConsultarCatalogo,
        {"status_vigencia": "todas"},
        True,
    ),
    (
        "catalogo.vigencia_fora_do_literal",
        EntradaConsultarCatalogo,
        {"status_vigencia": "caducada"},
        False,
    ),
    (
        "catalogo.numero_malformado",
        EntradaConsultarCatalogo,
        {"numero": "quatro mil"},
        False,
    ),
    (
        "catalogo.ano_impossivel",
        EntradaConsultarCatalogo,
        {"ano_minimo": 1200},
        False,
    ),
    (
        "crm.valido",
        EntradaRegistrarCrm,
        CRM_VALIDO,
        True,
    ),
    (
        "crm.campo_extra",
        EntradaRegistrarCrm,
        CRM_VALIDO | {"gravar_agora": True},
        False,
    ),
    (
        "crm.citacao_fora_do_formato",
        EntradaRegistrarCrm,
        CRM_VALIDO | {"normas_citadas": ["a norma que fala de incidente"]},
        False,
    ),
    (
        "crm.sem_norma_citada",
        EntradaRegistrarCrm,
        CRM_VALIDO | {"normas_citadas": []},
        False,
    ),
)


def c1_contratos() -> Resultado:
    """Schema válido: argumento bom passa, argumento ruim é recusado com detalhe.

    Sem corpus e sem LLM — o que se mede é o contrato Pydantic, e ele não depende
    de dado nenhum. É a verificação que sustenta o `extra='forbid'` do §7: sem
    ela, alguém relaxa a configuração do modelo e nada avisa.
    """
    falhas: list[str] = []
    for rotulo, modelo, argumentos, tem_de_passar in CASOS_DE_CONTRATO:
        try:
            modelo.model_validate(argumentos)
        except ValidationError as erro:
            if tem_de_passar:
                falhas.append(f"{rotulo}: recusou argumento válido")
            elif erro.error_count() == 0:
                falhas.append(f"{rotulo}: recusou sem apontar campo")
        else:
            if not tem_de_passar:
                falhas.append(f"{rotulo}: aceitou argumento inválido")
    return Resultado(
        id="c1.contratos",
        camada=1,
        descricao="contrato de tool aceita o válido e recusa o inválido com campo apontado",
        total=len(CASOS_DE_CONTRATO),
        falhas=tuple(falhas),
    )


def c1_prompt_cita_tools_reais(parametros: ParametrosDeTools) -> Resultado:
    """Todo nome de tool citado no prompt existe no registro, e vice-versa.

    Esta verificação nasceu de um defeito encontrado ao escrever a Fase 7: o
    prompt mandava chamar `consultar_catalogo_normas` e `registrar_consulta_crm`,
    e o registro expunha `consultar_catalogo` e `registrar_crm`. O modelo obedecia
    o prompt, o `despachar` respondia `tool_desconhecida` e a volta inteira do
    grafo era gasta na autocorreção — sem que nada ficasse vermelho, porque os
    testes de grafo montam registro próprio.

    A verificação vale nas duas direções de propósito. Nome citado que não existe
    queima uma volta; tool registrada que o prompt não menciona é ferramenta que
    o modelo nunca vai escolher — os dois são defeito, e o segundo é mais silencioso.
    """
    registro = montar_registro(
        recuperador=None,  # type: ignore[arg-type]
        catalogo=None,  # type: ignore[arg-type]
        parametros=parametros,
        thread_id="prompt",
    )
    nomes = set(registro)
    citados = {n for n in re.findall(r"\b[a-z_]{4,}\b", PROMPT_SISTEMA) if n in nomes}
    ausentes = sorted(nomes - citados)

    # Um identificador com underscore que o prompt cita e o registro não conhece
    # só pode ser nome de tool inventado: o texto restante é português.
    inventados = sorted(
        n
        for n in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", PROMPT_SISTEMA)
        if n not in nomes and n not in {"nao_sei"}
    )
    falhas = [f"prompt cita tool inexistente: {n}" for n in inventados]
    falhas += [f"tool registrada e não citada no prompt: {n}" for n in ausentes]
    return Resultado(
        id="c1.prompt_cita_tools_reais",
        camada=1,
        descricao="os nomes de tool no prompt e no registro são os mesmos, nos dois sentidos",
        total=len(nomes),
        falhas=tuple(falhas),
        detalhe={"tools_registradas": sorted(nomes)},
    )


def c1_orcamento_fixo(parametros: ParametrosDeTools, max_tokens_ctx: int) -> Resultado:
    """O custo fixo de contexto — prompt e schemas — cabe no orçamento do §13.

    Só o que não varia com o corpus entra aqui: prompt de sistema, os três
    schemas JSON das tools e a maior pergunta do gabarito. O que sobra é a folga
    disponível para trechos, e ela vai no detalhe porque é o número que encolhe
    quando alguém engorda uma descrição de tool.
    """
    esquemas = json.dumps(
        [e.parametros for e in _especificacoes_sem_corpus(parametros)],
        ensure_ascii=False,
    )
    perguntas = carregar_golden(GOLDEN)
    maior = max((p.pergunta for p in perguntas), key=len, default="")

    fixo = tokens_aprox(PROMPT_SISTEMA) + tokens_aprox(esquemas) + tokens_aprox(maior)
    falhas = (
        (f"custo fixo {fixo} >= max_tokens_ctx {max_tokens_ctx}",) if fixo >= max_tokens_ctx else ()
    )
    return Resultado(
        id="c1.orcamento_fixo",
        camada=1,
        descricao="prompt + schemas + maior pergunta cabem no orçamento de contexto",
        total=1,
        falhas=falhas,
        detalhe={
            "tokens_prompt": tokens_aprox(PROMPT_SISTEMA),
            "tokens_schemas": tokens_aprox(esquemas),
            "tokens_maior_pergunta": tokens_aprox(maior),
            "tokens_fixos": fixo,
            "max_tokens_ctx": max_tokens_ctx,
            "folga_para_trechos": max_tokens_ctx - fixo,
            "estimativa": f"~{CHARS_POR_TOKEN} caracteres por token, não tokenizador real",
        },
    )


def _especificacoes_sem_corpus(parametros: ParametrosDeTools):
    """As especificações das tools sem abrir índice nem catálogo.

    `montar_registro` recebe recuperador e conexão só para montar os `executar`;
    o que este orçamento mede é o `parametros` de cada tool, que sai do schema
    Pydantic e não toca dado. `None` chega aos executores e nunca é chamado.
    """
    registro = montar_registro(
        recuperador=None,  # type: ignore[arg-type]
        catalogo=None,  # type: ignore[arg-type]
        parametros=parametros,
        thread_id="orcamento",
    )
    return especificacoes(registro)


def c1_orcamento_real(
    registro,
    perguntas: Sequence[PerguntaDeGabarito],
    parametros: ParametrosDeTools,
    max_tokens_ctx: int,
) -> Resultado:
    """O orçamento com os trechos dentro — que é o que o modelo realmente recebe.

    **Esta verificação nasceu de um HTTP 413 em produção, e o custo fixo não o
    teria previsto.** O trace da primeira consulta real desta fase mostra a
    aritmética: a primeira chamada ao modelo custou 1.362 tokens de entrada; a
    segunda, depois de *uma* passagem por `buscar_normativo` com `k_final=5`,
    custou **7.655**; a terceira estourou. O free tier da Groq dá 8.000 tokens por
    minuto (`x-ratelimit-limit-tokens`), então cinco artigos inteiros de norma
    praticamente esgotam a janela sozinhos.

    Medir só prompt e schemas dava «folga de 6.725 tokens» e passava verde num
    sistema que não conseguia responder. O que engorda o contexto neste RAG não é
    o prompt: é o corpus.
    """
    falhas: list[str] = []
    esquemas = json.dumps(
        [e.parametros for e in _especificacoes_sem_corpus(parametros)], ensure_ascii=False
    )
    fixo = tokens_aprox(PROMPT_SISTEMA) + tokens_aprox(esquemas)
    medidos: list[int] = []

    for pergunta in [p for p in perguntas if p.tipo == "rag"][:5]:
        saida = registro[TOOL_BUSCAR].executar(EntradaBuscarNormativo(pergunta=pergunta.pergunta))
        trechos = tokens_aprox("".join(t.texto + t.citacao for t in saida.trechos))
        total = fixo + tokens_aprox(pergunta.pergunta) + trechos
        medidos.append(total)
        if total > max_tokens_ctx:
            falhas.append(f"{pergunta.id}: {total} tokens > max_tokens_ctx {max_tokens_ctx}")

    pior = max(medidos, default=0)
    return Resultado(
        id="c1.orcamento_real",
        camada=1,
        descricao="prompt + schemas + os trechos de uma busca cabem no orçamento de contexto",
        total=len(medidos),
        falhas=tuple(falhas),
        detalhe={
            "tokens_fixos": fixo,
            "pior_caso": pior,
            "media": round(sum(medidos) / len(medidos)) if medidos else 0,
            "max_tokens_ctx": max_tokens_ctx,
            "k_final_avaliado": "o padrão de config/parametros.toml",
            # O teto que manda de verdade não é o do TOML: é o do plano. Registrar
            # os dois lado a lado é o que impede a leitura de que 8.000 é folga.
            "nota": (
                "o free tier da Groq limita 8000 tokens/minuto somados a TODAS as "
                "chamadas; uma pergunta gasta várias voltas do agente"
            ),
            "estimativa": f"~{CHARS_POR_TOKEN} caracteres por token, não tokenizador real",
        },
    )


def c1_tools_respondem(registro, perguntas: Sequence[PerguntaDeGabarito]) -> Resultado:
    """As tools executam e devolvem exatamente o schema de saída declarado.

    É o equivalente local do "HTTP 200 das tools" do §9: aqui elas são chamadas
    de função, e o que corresponde a um 200 é devolver a saída tipada em vez de
    levantar. A tool de CRM fica de fora de propósito — ela é escrita,
    e escrita em eval é escrita.
    """
    falhas: list[str] = []
    amostra = [p for p in perguntas if p.tipo == "rag"][:5]
    for pergunta in amostra:
        resultado = registro["buscar_normativo"].executar(
            EntradaBuscarNormativo(pergunta=pergunta.pergunta)
        )
        if not isinstance(resultado, SaidaBuscarNormativo):
            falhas.append(f"{pergunta.id}: {TOOL_BUSCAR} devolveu {type(resultado).__name__}")

    catalogo = registro[TOOL_CATALOGO].executar(EntradaConsultarCatalogo(status_vigencia="todas"))
    if not isinstance(catalogo, SaidaConsultarCatalogo):
        falhas.append(f"{TOOL_CATALOGO} devolveu {type(catalogo).__name__}")
    elif catalogo.total == 0:
        falhas.append(f"{TOOL_CATALOGO} devolveu catálogo vazio")

    return Resultado(
        id="c1.tools_respondem",
        camada=1,
        descricao="as tools de leitura executam e devolvem a saída tipada declarada",
        total=len(amostra) + 1,
        falhas=tuple(falhas),
        detalhe={"normas_no_catalogo": getattr(catalogo, "total", 0)},
    )


def c1_citacao_no_contexto(registro, perguntas: Sequence[PerguntaDeGabarito]) -> Resultado:
    """O validador de citação discrimina sobre trechos reais, não sobre exemplo.

    Duas provas por pergunta, e as duas importam: a resposta que cita o trecho
    recuperado tem de ser **aprovada**, e a que cita norma que não veio na busca
    tem de ser **reprovada**. Só a primeira metade passaria com um validador que
    aprova tudo — que é o jeito mais fácil de o guardrail apodrecer sem ninguém
    notar.
    """
    falhas: list[str] = []
    avaliadas = 0
    orfa = "Conforme a Resolução BCB nº 999, de 1999, art. 1º, o prazo é de 30 dias."

    for pergunta in [p for p in perguntas if p.tipo == "rag"][:10]:
        saida = registro[TOOL_BUSCAR].executar(EntradaBuscarNormativo(pergunta=pergunta.pergunta))
        trechos: tuple[TrechoCitado, ...] = saida.trechos
        if not trechos:
            falhas.append(f"{pergunta.id}: busca não devolveu trecho algum")
            continue
        avaliadas += 1

        fiel = f"Conforme a {trechos[0].citacao}, aplica-se o disposto no artigo."
        if trechos[0].revogada:
            fiel += " A norma está revogada."
        if not validar_resposta(fiel, list(trechos)).aprovada:
            falhas.append(f"{pergunta.id}: reprovou citação que veio da própria busca")
        if validar_resposta(orfa, list(trechos)).aprovada:
            falhas.append(f"{pergunta.id}: aprovou citação órfã")

    return Resultado(
        id="c1.citacao_no_contexto",
        camada=1,
        descricao="citação recuperada é aprovada e citação órfã é reprovada, em dado real",
        total=avaliadas * 2,
        falhas=tuple(falhas),
    )


# --- camada 2: recuperação ---------------------------------------------------


def c2_recuperacao(recuperador, perguntas: Sequence[PerguntaDeGabarito], *, k: int) -> Resultado:
    """`recall@k` e MRR no modo de produção, contra o gabarito.

    Um modo só — o híbrido com re-ranking, que é o que atende de verdade. A
    comparação entre os três modos é da Fase 3 e mora em `ablacao.md`; repeti-la
    aqui geraria dois lugares onde o mesmo número pode divergir.
    """
    rag = [p for p in perguntas if p.tipo == "rag"]
    postos = [
        posto_do_acerto(
            recuperador.buscar(p.pergunta, modo="hibrido_rerank", k_final=k), p.esperado
        )
        for p in rag
    ]
    sem_acerto = tuple(p.id for p, posto in zip(rag, postos, strict=True) if posto is None)
    return Resultado(
        id="c2.recuperacao",
        camada=2,
        descricao=f"recall@{k} e MRR do pipeline de produção contra golden.jsonl",
        total=len(rag),
        # Não reprova sozinha: quem julga o número é o threshold do §13, aplicado
        # em `_vereditos`. A verificação só falha se ela não conseguir medir.
        falhas=(),
        detalhe={
            "k": k,
            "recall": round(recall(postos), 4),
            "mrr": round(mrr(postos), 4),
            "sem_acerto": list(sem_acerto),
        },
    )


# --- camada 3: LLM como juiz -------------------------------------------------

PROMPT_JUIZ = (
    "Você avalia a resposta de um copiloto normativo. Recebe a pergunta, a EVIDÊNCIA que as "
    "ferramentas devolveram ao sistema e a resposta que ele deu.\n"
    "A evidência pode vir de busca em texto de norma ou de consulta ao catálogo (metadados "
    "como vigência, ano e contagem). As duas contam igualmente como sustentação: uma resposta "
    "sobre quais normas estão revogadas se sustenta no catálogo, não em texto de artigo.\n"
    "Julgue dois critérios, cada um 1 (atende) ou 0 (não atende):\n"
    "- faithfulness: TODA afirmação da resposta é sustentada pelos trechos fornecidos. "
    "Se a resposta afirma algo que não está nos trechos, é 0. Recusar-se a responder por "
    "falta de base é 1, não 0.\n"
    "- relevancia: a resposta endereça a pergunta feita. Recusa honesta a uma pergunta "
    "fora do corpus é 1.\n"
    "Responda SOMENTE com JSON: "
    '{"faithfulness": 0 ou 1, "relevancia": 0 ou 1, "justificativa": "uma frase"}'
)


@dataclass(slots=True)
class Juiz:
    """O quarto sistema de IA do repositório, nomeado como tal (§16.1).

    Envolve um `ProvedorLLM` e troca a identidade: o trace e o inventário
    precisam distinguir o modelo que responde ao cliente do modelo que julga o
    primeiro. Mesmo fornecedor e mesma fatura não fazem deles o mesmo sistema —
    eles têm propósitos, riscos e critérios de reavaliação diferentes.
    """

    provedor: ProvedorLLM
    id_sistema: str = ID_SISTEMA_JUIZ
    truncamentos: int = 0

    def julgar(
        self, pergunta: str, resposta: str, evidencias: Sequence[tuple[str, str]]
    ) -> list[Nota]:
        """Julga contra o que as tools devolveram — toda a evidência, e só ela.

        **A primeira versão disto media a cegueira do juiz, não o agente.** Ela
        passava só os `trechos` de `buscar_normativo`, então as perguntas de
        catálogo (`tipo == "sql"`) chegavam com «(nenhum trecho)» e eram obrigadas
        a tirar zero: g38 e g39 foram reprovadas por citar o catálogo, que é
        exatamente onde a resposta delas mora. E como o `revogada` do trecho não
        ia junto, g02 foi reprovada por afirmar vigência — um dado que a tool
        devolveu e o juiz não recebeu. `faithfulness_juiz` deu 0,25 enquanto o
        validador determinístico dava 1,0, e quem estava errado era o juiz.

        A regra que sobra: **o juiz vê o que o agente viu.** Menos que isso mede o
        avaliador.
        """
        self.truncamentos += sum(1 for _, saida in evidencias if len(saida) > LIMITE_DE_EVIDENCIA)
        contexto = (
            "\n\n".join(f"[{nome}]\n{_encurtar(saida)}" for nome, saida in evidencias)
            or "(nenhuma evidência: o sistema não chamou ferramenta alguma)"
        )
        conteudo = (
            f"PERGUNTA: {pergunta}\n\nEVIDÊNCIA DAS FERRAMENTAS:\n{contexto}\n\n"
            f"RESPOSTA: {resposta}"
        )
        saida = self.provedor.gerar(
            [Mensagem.sistema(PROMPT_JUIZ), Mensagem.usuario(conteudo)], temperatura=0.0
        )
        return _notas_de(saida.conteudo)


# Teto por evidência. O free tier dá 8.000 tokens por minuto somados, e o juiz é
# a segunda chamada paga da mesma pergunta: mandar a evidência inteira faz o
# julgamento estourar justamente nas perguntas mais ricas, que são as que mais
# valem julgar. O corte é anunciado no texto para o juiz não penalizar ausência
# que ele mesmo pode ver que foi cortada.
#
# **E o corte contamina a métrica quando morde.** Com 2.000 caracteres, o juiz
# reprovou g39 por «a evidência só lista 3 normas» quando a resposta dizia 5 — as
# outras duas linhas do catálogo tinham sido cortadas por este limite, não
# inventadas pelo agente. Por isso `truncamentos` é contado e publicado junto do
# número: um `faithfulness_juiz` medido sobre evidência cortada mede o corte.
LIMITE_DE_EVIDENCIA = 6000


def _encurtar(texto: str) -> str:
    if len(texto) <= LIMITE_DE_EVIDENCIA:
        return texto
    return texto[:LIMITE_DE_EVIDENCIA] + "\n…[evidência truncada para caber no orçamento]"


def _notas_de(bruto: str) -> list[Nota]:
    """O JSON do juiz vira notas. Resposta ilegível é zero, não é ausência.

    Um juiz que devolveu lixo não avaliou — e tratar isso como "sem dado" faria a
    média subir justamente quando o avaliador falha. Zero com a justificativa
    `juiz_ilegivel` mantém o caso visível no relatório.
    """
    texto = bruto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        dados = json.loads(texto)
        justificativa = str(dados.get("justificativa", ""))[:300]
        return [
            Nota("", "faithfulness", float(dados["faithfulness"]), justificativa),
            Nota("", "relevancia", float(dados["relevancia"]), justificativa),
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return [
            Nota("", "faithfulness", 0.0, "juiz_ilegivel"),
            Nota("", "relevancia", 0.0, "juiz_ilegivel"),
        ]


# A tool que cada tipo de pergunta deveria acionar. É contrato do §9: pergunta de
# metadado é consulta exata, não busca por similaridade — errar isso é o defeito
# clássico de RAG genérico, e por isso ele é medido por código, não pelo juiz.
TOOL_ESPERADA = {"rag": TOOL_BUSCAR, "sql": TOOL_CATALOGO}


def c3_agente(
    *,
    executar: Callable[[PerguntaDeGabarito], tuple[dict[str, Any], SessaoContadora]],
    juiz: Juiz | None,
    perguntas: Sequence[PerguntaDeGabarito],
    pausa: float = 0.0,
    dormir: Callable[[float], None] = time.sleep,
) -> Resultado:
    """Roda o grafo inteiro sobre a amostra e colhe as quatro métricas do §9.

    Faithfulness aparece duas vezes de propósito: `faithfulness_citacao` é o
    veredito determinístico do guardrail de saída (a citação saiu dos trechos
    desta execução?) e `faithfulness_juiz` é o do LLM (a afirmação é sustentada
    pelo trecho?). São perguntas diferentes, e é honesto dizer qual delas está
    sustentando qual alegação — ver o cabeçalho de `metricas.py`.
    """
    notas: list[Nota] = []
    chamadas = erros = 0
    fieis = respondidas = 0
    tool_certa = tool_avaliada = 0
    nao_sei_certas = nao_sei_total = 0
    falhas: list[str] = []
    indisponibilidades: list[str] = []

    for indice, pergunta in enumerate(perguntas):
        # O free tier da Groq limita tokens por minuto, e o backoff de
        # `resiliencia.py` sobe só até ~15 s — menos que a janela de reset. Sem
        # pausa entre perguntas, a camada 3 mede o rate limit em vez de medir o
        # agente: na primeira execução desta fase, 7 de 8 perguntas morreram em
        # HTTP 429 e a única que passou tirou nota cheia.
        if indice and pausa:
            dormir(pausa)
        try:
            final, sessao = executar(pergunta)
        except ErroDeProvedor as erro:
            # Provedor fora do ar ou cota estourada é propriedade do ambiente, não
            # do agente. Somar isso às falhas do agente faria a fase reprovar por
            # causa do free tier — e faria a métrica mentir nos dois sentidos.
            logger.warning("provedor indisponível", extra={"pergunta": pergunta.id})
            indisponibilidades.append(f"{pergunta.id}: {erro}")
            continue
        except Exception as erro:  # noqa: BLE001 — eval que morre no meio não mede nada
            logger.exception("execução falhou", extra={"pergunta": pergunta.id})
            falhas.append(f"{pergunta.id}: {type(erro).__name__}: {erro}")
            continue

        chamadas += len(sessao.chamadas)
        erros += len(sessao.erros)

        resposta = final.get("resposta", "")
        trechos = list(final.get("trechos", []))
        recusou = resposta.strip() == SEM_BASE_NORMATIVA

        respondidas += 1
        if validar_resposta(resposta, [TrechoCitado(**t) for t in trechos]).aprovada:
            fieis += 1

        esperada = TOOL_ESPERADA.get(pergunta.tipo)
        if esperada is not None:
            tool_avaliada += 1
            if esperada in sessao.chamadas:
                tool_certa += 1

        if pergunta.tipo == "nao_sei":
            nao_sei_total += 1
            if recusou:
                nao_sei_certas += 1

        if juiz is not None:
            # O juiz é a segunda chamada paga da mesma pergunta e é o mais provável
            # de bater no rate limit — a primeira já consumiu cota. Deixá-lo fora do
            # `try` fazia o eval inteiro morrer na primeira recusa e não gravar nada:
            # foi assim que a segunda execução desta fase se perdeu depois de já ter
            # medido tudo o que não dependia dele.
            try:
                for nota in juiz.julgar(pergunta.pergunta, resposta, sessao.evidencias):
                    notas.append(Nota(pergunta.id, nota.criterio, nota.valor, nota.justificativa))
            except ErroDeProvedor as erro:
                logger.warning("juiz indisponível", extra={"pergunta": pergunta.id})
                indisponibilidades.append(f"{pergunta.id} (juiz): {erro}")

    medias = por_criterio(notas)
    return Resultado(
        id="c3.agente",
        camada=3,
        descricao="grafo completo sobre amostra do gabarito, julgado por código e por LLM",
        total=len(perguntas),
        falhas=tuple(falhas),
        detalhe={
            "amostra": [p.id for p in perguntas],
            "medidas": respondidas,
            "indisponibilidades": indisponibilidades,
            "faithfulness_citacao": round(taxa(fieis, respondidas), 4),
            "faithfulness_juiz": round(medias.get("faithfulness", 0.0), 4),
            "relevancia_juiz": round(medias.get("relevancia", 0.0), 4),
            "juiz": juiz.id_sistema if juiz is not None else "nao_executado",
            "notas_do_juiz": len(notas),
            "evidencias_truncadas": getattr(juiz, "truncamentos", 0),
            # A justificativa de cada nota vai junto porque nota sem justificativa
            # não é revisável — está no cabeçalho de `metricas.py` e vale para o
            # juiz também. Sem isto, um `faithfulness_juiz` de 0,25 é um número
            # que não dá para investigar: não se sabe se o agente falhou ou se o
            # juiz está errado, e essas duas conclusões levam a lugares opostos.
            "vereditos_do_juiz": [
                {
                    "pergunta": n.id_pergunta,
                    "criterio": n.criterio,
                    "valor": n.valor,
                    "justificativa": n.justificativa,
                }
                for n in notas
            ],
            "media_geral_do_juiz": round(media(notas), 4),
            "chamadas_de_tool": chamadas,
            "chamadas_recusadas_no_contrato": erros,
            "taxa_erro_tool": round(taxa_de_erro_de_tool(chamadas, erros), 4),
            "acuracia_escolha_de_tool": round(taxa(tool_certa, tool_avaliada), 4),
            "recusas_corretas_em_nao_sei": f"{nao_sei_certas}/{nao_sei_total}",
        },
    )


# --- montagem ----------------------------------------------------------------


def amostra_estratificada(perguntas: Sequence[PerguntaDeGabarito], tamanho: int):
    """A amostra da camada 3, sem sorteio.

    Amostra aleatória tornaria duas execuções incomparáveis, e comparar execuções
    é todo o ponto de medir. A ordem é por grupo e depois por id: o resultado é
    reprodutível e cobre `nao_sei`, `revogada` e `sql`, que são os casos que o §9
    diz separarem um agente de um chatbot.
    """
    por_grupo: dict[str, list[PerguntaDeGabarito]] = {}
    for pergunta in sorted(perguntas, key=lambda p: p.id):
        por_grupo.setdefault(pergunta.grupo or pergunta.tipo, []).append(pergunta)

    escolhidas: list[PerguntaDeGabarito] = []
    while len(escolhidas) < tamanho and any(por_grupo.values()):
        for grupo in sorted(por_grupo):
            if por_grupo[grupo] and len(escolhidas) < tamanho:
                escolhidas.append(por_grupo[grupo].pop(0))
    return sorted(escolhidas, key=lambda p: p.id)


def _commit() -> str:
    """O commit em que a medição rodou. Número sem procedência não vale nada."""
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


def tem_corpus() -> bool:
    """`/data` e `/indices` são gitignored: no CI eles simplesmente não existem."""
    return CATALOGO.exists() and (RAIZ / "indices" / "bm25.json").exists()


def _vereditos(resultados: Sequence[Resultado], thresholds: dict[str, float]) -> dict[str, Any]:
    """Os thresholds do §13 aplicados ao que foi medido — e só ao que foi medido.

    Métrica de camada que não rodou não vira "reprovado" nem "aprovado": vira
    `nao_medido`. Um relatório que reprova o que não mediu ensina a ignorar
    relatório.
    """
    por_id = {r.id: r for r in resultados if r.executada}
    vereditos: dict[str, Any] = {}

    recuperacao = por_id.get("c2.recuperacao")
    vereditos["recall_5"] = (
        contra_threshold(recuperacao.detalhe["recall"], thresholds["recall_5_min"])
        if recuperacao
        else "nao_medido"
    )

    agente = por_id.get("c3.agente")
    if agente:
        vereditos["faithfulness_citacao"] = contra_threshold(
            agente.detalhe["faithfulness_citacao"], thresholds["faithfulness_min"]
        )
        vereditos["taxa_erro_tool"] = contra_teto(
            agente.detalhe["taxa_erro_tool"], thresholds["erro_tool_max"]
        )
        if agente.detalhe["notas_do_juiz"]:
            vereditos["faithfulness_juiz"] = contra_threshold(
                agente.detalhe["faithfulness_juiz"], thresholds["faithfulness_min"]
            )
            vereditos["relevancia_juiz"] = contra_threshold(
                agente.detalhe["relevancia_juiz"], thresholds["relevancia_min"]
            )
    else:
        vereditos["faithfulness_citacao"] = "nao_medido"
        vereditos["taxa_erro_tool"] = "nao_medido"
    return vereditos


def limitacoes(resultados: Sequence[Resultado]) -> list[str]:
    """O que esta execução NÃO conseguiu medir, derivado dela mesma.

    Escrito por código e não à mão, pela mesma razão que a leitura da tabela de
    ablação é calculada: uma ressalva redigida ao lado do número sobrevive à
    medição que a desminta. Aqui ela desaparece sozinha quando a causa some.

    Sem este bloco, `faithfulness_juiz` aparece no relatório como um número
    comparável aos outros — e ele não é, quando metade da amostra se perdeu no
    rate limit e parte da evidência chegou cortada ao avaliador.
    """
    avisos: list[str] = []
    por_id = {r.id: r for r in resultados if r.executada}

    agente = por_id.get("c3.agente")
    if agente:
        perdidas = len(agente.detalhe.get("indisponibilidades", []))
        medidas = agente.detalhe.get("medidas", 0)
        if perdidas:
            avisos.append(
                f"{perdidas} de {agente.total} perguntas da camada 3 não foram medidas: o free "
                "tier da Groq recusou a chamada (HTTP 429/413, teto de 8.000 tokens/minuto). "
                f"Toda métrica de camada 3 vale sobre n={medidas}."
            )
        cortadas = agente.detalhe.get("evidencias_truncadas", 0)
        if cortadas:
            avisos.append(
                f"{cortadas} evidência(s) chegaram truncadas ao juiz por causa do mesmo teto. "
                "Onde o corte morde, `faithfulness_juiz` mede o corte, não o agente — a nota "
                "determinística `faithfulness_citacao` não sofre disso."
            )
        if medidas and medidas < 10:
            avisos.append(
                f"n={medidas} é amostra pequena demais para os thresholds do §13 serem "
                "conclusivos; eles são reportados, não afirmados."
            )

    orcamento = por_id.get("c1.orcamento_real")
    if orcamento and orcamento.falhas:
        avisos.append(
            f"o orçamento real de contexto estoura no pior caso "
            f"({orcamento.detalhe.get('pior_caso')} tokens contra "
            f"{orcamento.detalhe.get('max_tokens_ctx')}): há perguntas que o free tier não "
            "consegue responder sem baixar `k_final` ou truncar o texto do trecho."
        )
    return avisos


def montar_relatorio(payload: dict[str, Any]) -> str:
    """O relatório para gente, derivado do mesmo payload que vai para o JSON."""
    linhas = [
        "# Relatório de avaliação",
        "",
        "Gerado por `evals/rodar.py`. **Não editar à mão** — o próximo `rodar.py` sobrescreve,",
        "e um número editado aqui deixa de ter execução por trás.",
        "",
        f"- Medido em: {payload['medido_em']}",
        f"- Commit: `{payload['commit']}`",
        f"- Camadas pedidas: {', '.join(str(c) for c in payload['camadas'])}",
        f"- Corpus disponível: {'sim' if payload['corpus'] else 'não'}",
        f"- `versao_ficha`: `{payload['versao_ficha']}`",
        "",
        "## Verificações",
        "",
        "| Camada | Verificação | Situação | Falhas |",
        "|---|---|---|---|",
    ]
    for r in payload["verificacoes"]:
        if not r["executada"]:
            situacao = f"pulada ({r['motivo']})"
        else:
            situacao = "verde" if r["aprovada"] else "**VERMELHA**"
        linhas.append(f"| {r['camada']} | `{r['id']}` | {situacao} | {len(r['falhas'])} |")

    linhas += ["", "## Métricas contra os thresholds de `parametros.toml`", ""]
    linhas += ["| Métrica | Medido | Exigido | Veredito |", "|---|---|---|---|"]
    for nome, veredito in payload["vereditos"].items():
        if veredito == "nao_medido":
            linhas.append(f"| `{nome}` | — | — | não medido nesta execução |")
            continue
        limite = veredito.get("minimo", veredito.get("maximo"))
        comparador = "≥" if "minimo" in veredito else "≤"
        marca = "verde" if veredito["aprovado"] else "**VERMELHO**"
        linhas.append(f"| `{nome}` | {veredito['valor']} | {comparador} {limite} | {marca} |")

    if payload.get("limitacoes"):
        linhas += ["", "## O que esta execução não mediu", ""]
        linhas += [f"- {aviso}" for aviso in payload["limitacoes"]]

    for r in payload["verificacoes"]:
        if r["falhas"]:
            linhas += ["", f"### Falhas em `{r['id']}`", ""]
            linhas += [f"- {f}" for f in r["falhas"]]

    detalhes = {r["id"]: r["detalhe"] for r in payload["verificacoes"] if r["detalhe"]}
    if detalhes:
        linhas += ["", "## Detalhe medido", "", "```json"]
        linhas.append(json.dumps(detalhes, ensure_ascii=False, indent=2))
        linhas.append("```")
    linhas.append("")
    return "\n".join(linhas)


def executar_camadas(
    camadas: Sequence[int], *, tamanho_amostra: int, pausa: float = 0.0
) -> dict[str, Any]:
    """Roda o que foi pedido e o ambiente permite, e devolve o payload completo."""
    dados = tomllib.loads(TOML.read_text(encoding="utf-8"))
    thresholds = dados["evals"]
    max_tokens_ctx = int(dados["llm"]["max_tokens_ctx"])
    k_final = int(dados["recuperacao"]["k_final"])
    ptools = parametros_de_tools(TOML)
    perguntas = carregar_golden(GOLDEN)
    corpus = tem_corpus()

    resultados: list[Resultado] = []
    fechaveis: list[Any] = []

    try:
        if 1 in camadas:
            resultados.append(c1_contratos())
            resultados.append(c1_prompt_cita_tools_reais(ptools))
            resultados.append(c1_orcamento_fixo(ptools, max_tokens_ctx))

        precisa_corpus = bool({1, 2, 3} & set(camadas))
        recuperador = catalogo = registro = None
        if precisa_corpus and corpus:
            from copiloto.recuperacao import Recuperador

            catalogo = sqlite3.connect(CATALOGO, check_same_thread=False)
            catalogo.row_factory = sqlite3.Row
            fechaveis.append(catalogo)
            recuperador = Recuperador.abrir(RAIZ, catalogo=catalogo)
            registro = montar_registro(
                recuperador=recuperador,
                catalogo=catalogo,
                parametros=ptools,
                thread_id="eval",
            )

        sem_corpus = "corpus ausente (/data e /indices são gitignored)"
        if 1 in camadas:
            if registro is not None:
                resultados.append(c1_orcamento_real(registro, perguntas, ptools, max_tokens_ctx))
                resultados.append(c1_tools_respondem(registro, perguntas))
                resultados.append(c1_citacao_no_contexto(registro, perguntas))
            else:
                resultados.append(
                    pulada("c1.orcamento_real", 1, "orçamento com trechos dentro", sem_corpus)
                )
                resultados.append(
                    pulada("c1.tools_respondem", 1, "as tools executam e devolvem", sem_corpus)
                )
                resultados.append(
                    pulada("c1.citacao_no_contexto", 1, "validador em dado real", sem_corpus)
                )

        if 2 in camadas:
            if recuperador is not None:
                resultados.append(c2_recuperacao(recuperador, perguntas, k=k_final))
            else:
                resultados.append(
                    pulada("c2.recuperacao", 2, "recall e MRR contra o gabarito", sem_corpus)
                )

        if 3 in camadas:
            if registro is None or recuperador is None:
                resultados.append(pulada("c3.agente", 3, "grafo completo julgado", sem_corpus))
            else:
                resultados.append(
                    _rodar_camada_3(
                        perguntas=amostra_estratificada(perguntas, tamanho_amostra),
                        pausa=pausa,
                        recuperador=recuperador,
                        catalogo=catalogo,
                        ptools=ptools,
                    )
                )
    finally:
        for recurso in fechaveis:
            recurso.close()

    return {
        "medido_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _commit(),
        "camadas": list(camadas),
        "corpus": corpus,
        "versao_ficha": versao_da_ficha(RAIZ),
        "thresholds": thresholds,
        "verificacoes": [r.como_dicionario() for r in resultados],
        "vereditos": _vereditos(resultados, thresholds),
        "limitacoes": limitacoes(resultados),
    }


def _rodar_camada_3(
    *,
    perguntas: Sequence[PerguntaDeGabarito],
    pausa: float,
    recuperador,
    catalogo,
    ptools: ParametrosDeTools,
) -> Resultado:
    """Monta o grafo de avaliação e o juiz. Sem CRM: eval não escreve fora.

    `escrever=None` não é economia — é a mesma regra do §7. Se a amostra fizer o
    agente propor um registro, o grafo para no `interrupt()` e o eval registra
    isso; ele não aprova nada em nome de ninguém.
    """
    from langgraph.checkpoint.memory import MemorySaver

    from copiloto.api.main import provedor_do_ambiente

    provedor = provedor_do_ambiente("groq")
    juiz = Juiz(provedor=provedor_do_ambiente("groq"))
    parametros = parametros_do_grafo(TOML)
    # Modelo e provedor entram no resultado pelo mesmo motivo que o commit entra:
    # faithfulness de 0,9 não significa nada sem dizer *de qual modelo*. Trocar o
    # `GROQ_MODEL` e comparar com a medição anterior sem esta linha compararia
    # coisas diferentes achando que compara a mesma.
    procedencia = {
        "id_sistema": provedor.id_sistema,
        "modelo": getattr(provedor, "modelo", ""),
        "id_sistema_juiz": juiz.id_sistema,
        "modelo_juiz": getattr(juiz.provedor, "modelo", ""),
    }

    def executar(pergunta: PerguntaDeGabarito) -> tuple[dict[str, Any], SessaoContadora]:
        sessao = SessaoContadora()
        registro = montar_registro(
            recuperador=recuperador,
            catalogo=catalogo,
            parametros=ptools,
            thread_id=f"eval{pergunta.id}",
        )
        grafo = compilar(
            Dependencias(
                provedor=provedor,
                registro=registro,
                parametros=parametros,
                escrever=None,
                sessao=sessao,
            ),
            checkpointer=MemorySaver(),
        )
        final = grafo.invoke(
            {"pergunta": pergunta.pergunta, "thread_id": f"eval{pergunta.id}"},
            {"configurable": {"thread_id": f"eval{pergunta.id}"}},
        )
        return dict(final), sessao

    resultado = c3_agente(executar=executar, juiz=juiz, perguntas=perguntas, pausa=pausa)
    resultado.detalhe |= procedencia
    return resultado


def main() -> int:
    analisador = argparse.ArgumentParser(description="Avaliação em três camadas (§9 do briefing).")
    analisador.add_argument(
        "--camada",
        type=int,
        nargs="+",
        choices=(1, 2, 3),
        default=[1],
        help="quais camadas rodar (padrão: 1, a que roda no CI)",
    )
    analisador.add_argument("--todas", action="store_true", help="atalho para --camada 1 2 3")
    analisador.add_argument(
        "--amostra", type=int, default=8, help="perguntas na camada 3 (padrão: 8)"
    )
    analisador.add_argument(
        "--pausa",
        type=float,
        default=25.0,
        help="segundos entre perguntas da camada 3, para não estourar o free tier (padrão: 25)",
    )
    analisador.add_argument("--json", type=Path, default=RAIZ / "evals" / "resultados.json")
    analisador.add_argument("--relatorio", type=Path, default=RAIZ / "evals" / "relatorio.md")
    argumentos = analisador.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    camadas = [1, 2, 3] if argumentos.todas else sorted(set(argumentos.camada))

    payload = executar_camadas(camadas, tamanho_amostra=argumentos.amostra, pausa=argumentos.pausa)
    argumentos.json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    argumentos.relatorio.write_text(montar_relatorio(payload), encoding="utf-8")

    # `print` porque isto é script de linha de comando e a saída é o produto; a
    # proibição do §11 vale para o código de produção.
    for verificacao in payload["verificacoes"]:
        if not verificacao["executada"]:
            estado = f"PULADA   ({verificacao['motivo']})"
        elif verificacao["aprovada"]:
            estado = "verde"
        else:
            estado = f"FALHOU ({len(verificacao['falhas'])})"
        print(f"camada {verificacao['camada']}  {verificacao['id']:26} {estado}")
    for nome, veredito in payload["vereditos"].items():
        if veredito == "nao_medido":
            print(f"           {nome:26} não medido")
        else:
            print(
                f"           {nome:26} {veredito['valor']} "
                f"{'ok' if veredito['aprovado'] else 'ABAIXO DO EXIGIDO'}"
            )
    print(f"relatório em {argumentos.relatorio.relative_to(RAIZ)}")

    reprovou = any(not v["aprovada"] for v in payload["verificacoes"] if v["executada"]) or any(
        isinstance(v, dict) and not v["aprovado"] for v in payload["vereditos"].values()
    )
    return 1 if reprovou else 0


if __name__ == "__main__":
    raise SystemExit(main())
