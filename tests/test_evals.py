"""A régua dos evals, medida antes de medir com ela.

Duas coisas são testadas aqui e elas são diferentes:

* **`metricas.py`** — as contas. Recall e MRR sobre postos conhecidos, onde a
  resposta certa é calculável à mão. Se a régua estiver torta, todo número da
  Fase 3 e da Fase 7 está torto junto.
* **A camada 1 de `rodar.py`** — as verificações que rodam no CI a cada push.
  Elas não podem depender de `/data` nem de `/indices`, que são gitignored; este
  arquivo é o que garante isso, porque roda no mesmo runner sem corpus.

O que **não** está aqui: as camadas 2 e 3. Elas exigem corpus e, a 3, chamada
paga de LLM. Ficam marcadas `corpus` no arquivo próprio, e o relatório da fase
diz que rodaram na máquina.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from evals import rodar
from evals.metricas import (
    Nota,
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
from evals.rodar import Reidratacao, Resultado, limitacoes

from copiloto.llm.provedor import ErroDeProvedor

RAIZ = Path(__file__).resolve().parents[1]
GOLDEN = RAIZ / "evals" / "golden.jsonl"


@dataclass(frozen=True)
class TrechoFalso:
    id_norma: str
    numero_artigo: int


# --- as contas ---------------------------------------------------------------


def test_posto_do_acerto_e_1_based_e_pega_o_primeiro() -> None:
    trechos = [TrechoFalso("a", 1), TrechoFalso("b", 2), TrechoFalso("c", 3)]
    assert posto_do_acerto(trechos, frozenset({("b", 2)})) == 2
    assert posto_do_acerto(trechos, frozenset({("b", 2), ("c", 3)})) == 2
    assert posto_do_acerto(trechos, frozenset({("z", 9)})) is None


def test_recall_e_mrr_sobre_postos_calculaveis_a_mao() -> None:
    """1, 2 e nenhum: recall 2/3; MRR (1 + 0,5 + 0)/3."""
    postos = [1, 2, None]
    assert recall(postos) == pytest.approx(2 / 3)
    assert mrr(postos) == pytest.approx((1 + 0.5) / 3)


def test_metricas_de_lista_vazia_sao_zero_e_nao_erro() -> None:
    """Sem amostra o número é zero, e o relatório diz que a amostra é zero."""
    assert recall([]) == 0.0
    assert mrr([]) == 0.0
    assert taxa(3, 0) == 0.0


def test_taxa_de_erro_de_tool_conta_sobre_o_total_de_chamadas() -> None:
    assert taxa_de_erro_de_tool(50, 1) == pytest.approx(0.02)


def test_por_criterio_separa_as_medias_do_juiz() -> None:
    notas = [
        Nota("g01", "faithfulness", 1.0),
        Nota("g02", "faithfulness", 0.0),
        Nota("g01", "relevancia", 1.0),
    ]
    assert por_criterio(notas) == {"faithfulness": 0.5, "relevancia": 1.0}
    assert media(notas) == pytest.approx(2 / 3)


def test_veredito_e_calculado_e_nao_digitado() -> None:
    """O booleano sai da comparação; ninguém escreve `aprovado: true` à mão."""
    assert contra_threshold(0.95, 0.95)["aprovado"] is True
    assert contra_threshold(0.949, 0.95)["aprovado"] is False
    assert contra_teto(0.02, 0.02)["aprovado"] is True
    assert contra_teto(0.021, 0.02)["aprovado"] is False


def test_golden_carrega_as_quarenta_e_filtra_por_tipo() -> None:
    assert len(carregar_golden(GOLDEN)) == 40
    rag = carregar_golden(GOLDEN, tipo="rag")
    assert len(rag) == 32
    assert all(p.esperado for p in rag)


# --- camada 1: o que roda no CI ----------------------------------------------


def test_camada_1_contratos_passa_sem_corpus() -> None:
    """Os contratos das tools não dependem de dado nenhum — é o ponto deles."""
    resultado = rodar.c1_contratos()
    assert resultado.aprovada, resultado.falhas
    assert resultado.total == len(rodar.CASOS_DE_CONTRATO)


def test_camada_1_reprova_se_o_contrato_afrouxar(monkeypatch: pytest.MonkeyPatch) -> None:
    """A verificação tem de saber falhar. Sem isto ela é decoração verde.

    O caso injetado é o modo de falha real: alguém troca `extra='forbid'` por
    `extra='ignore'` num schema e o campo a mais deixa de ser recusado.
    """
    from pydantic import BaseModel, ConfigDict

    class Afrouxado(BaseModel):
        model_config = ConfigDict(extra="ignore")
        pergunta: str = ""

    casos = (("falso.aceita_campo_extra", Afrouxado, {"campo_inventado": "x"}, False),)
    monkeypatch.setattr(rodar, "CASOS_DE_CONTRATO", casos)
    assert not rodar.c1_contratos().aprovada


def test_camada_1_prompt_cita_apenas_tools_existentes(parametros_de_tools) -> None:
    """A regressão do defeito que a Fase 7 encontrou.

    O prompt mandava chamar `consultar_catalogo_normas`; o registro expõe
    `consultar_catalogo`. Toda pergunta de metadado queimava uma volta do grafo
    em `tool_desconhecida` — e os testes de grafo não viam, porque montam
    registro próprio.
    """
    resultado = rodar.c1_prompt_cita_tools_reais(parametros_de_tools)
    assert resultado.aprovada, resultado.falhas


def test_camada_1_orcamento_fixo_cabe_no_contexto(parametros_de_tools) -> None:
    resultado = rodar.c1_orcamento_fixo(parametros_de_tools, 8000)
    assert resultado.aprovada, resultado.falhas
    assert resultado.detalhe["folga_para_trechos"] > 0


def test_camada_1_orcamento_reprova_quando_nao_cabe(parametros_de_tools) -> None:
    assert not rodar.c1_orcamento_fixo(parametros_de_tools, 10).aprovada


@pytest.fixture
def parametros_de_tools():
    from copiloto.tools.registro import carregar_parametros

    return carregar_parametros(RAIZ / "config" / "parametros.toml")


# --- o que acontece quando falta corpus --------------------------------------


def test_verificacao_pulada_nao_aprova_nem_reprova() -> None:
    """Pulada é um terceiro estado. Contá-la como verde é o jeito de mentir."""
    resultado = rodar.pulada("c2.recuperacao", 2, "recall", "corpus ausente")
    assert resultado.executada is False
    assert resultado.como_dicionario()["motivo"] == "corpus ausente"


def test_veredito_de_metrica_nao_medida_e_explicito() -> None:
    """`nao_medido` no lugar de um número inventado, e o relatório imprime isso."""
    vereditos = rodar._vereditos(
        [], {"recall_5_min": 0.8, "faithfulness_min": 0.95, "erro_tool_max": 0.02}
    )
    assert vereditos["recall_5"] == "nao_medido"
    assert vereditos["faithfulness_citacao"] == "nao_medido"


def test_relatorio_marca_pulada_e_nao_medida() -> None:
    payload = {
        "medido_em": "2026-09-10T00:00:00+00:00",
        "commit": "abc1234",
        "camadas": [1],
        "corpus": False,
        "versao_ficha": "nao-declarada",
        "thresholds": {},
        "verificacoes": [
            rodar.pulada("c2.recuperacao", 2, "recall", "corpus ausente").como_dicionario()
        ],
        "vereditos": {"recall_5": "nao_medido"},
    }
    texto = rodar.montar_relatorio(payload)
    assert "pulada (corpus ausente)" in texto
    assert "não medido nesta execução" in texto


# --- a camada 3, nas partes que não custam chamada ---------------------------


def test_juiz_ilegivel_vale_zero_e_nao_ausencia() -> None:
    """Juiz que devolveu lixo não avaliou — e não pode fazer a média subir."""
    notas = rodar._notas_de("desculpe, não consigo avaliar")
    assert [n.valor for n in notas] == [0.0, 0.0]
    assert all(n.justificativa == "juiz_ilegivel" for n in notas)


def test_juiz_em_cerca_de_markdown_e_lido() -> None:
    """Modelo que embrulha JSON em ```json é o caso comum, não a exceção."""
    bruto = "```json\n" + json.dumps({"faithfulness": 1, "relevancia": 0, "justificativa": "ok"})
    bruto += "\n```"
    assert {n.criterio: n.valor for n in rodar._notas_de(bruto)} == {
        "faithfulness": 1.0,
        "relevancia": 0.0,
    }


def test_amostra_e_reproduzivel_e_cobre_os_casos_dificeis() -> None:
    """Sem sorteio: duas execuções têm de ser comparáveis, ou medir não serve.

    E a amostra tem de alcançar `nao_sei` e `sql` — são os casos que o §9 diz
    separarem um agente de um chatbot, e são os primeiros a sumir de uma amostra
    ingênua que pega as N primeiras linhas.
    """
    perguntas = carregar_golden(GOLDEN)
    primeira = rodar.amostra_estratificada(perguntas, 8)
    assert [p.id for p in primeira] == [p.id for p in rodar.amostra_estratificada(perguntas, 8)]
    tipos = {p.tipo for p in primeira}
    assert "nao_sei" in tipos and "sql" in tipos


def test_sessao_contadora_conta_pela_mesma_superficie_do_trace() -> None:
    """Ela recebe o mesmo `output` que o span do Langfuse recebe do `deliberar`.

    Se alguém mudar o formato de `_resumo_do_ciclo` sem mexer aqui, a contagem
    para de bater — que é exatamente o alarme desejado.
    """
    sessao = rodar.SessaoContadora()
    with sessao.no("deliberar.ciclo") as observacao:
        observacao.atualizar(
            output={
                "tools_chamadas": ["buscar_normativo", "buscar_normativo"],
                "erros_de_tool": [{"tool": "buscar_normativo", "tipo": "schema_invalido"}],
            }
        )
    with sessao.no("sanear") as observacao:
        observacao.atualizar(output={"passo": 1})

    assert len(sessao.chamadas) == 2
    assert len(sessao.erros) == 1
    assert taxa_de_erro_de_tool(len(sessao.chamadas), len(sessao.erros)) == pytest.approx(0.5)


def test_camada_1_completa_roda_sem_corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """O que o CI faz, feito aqui: `--camada 1` num ambiente sem `/data`.

    É a verificação que impede a regressão mais provável desta fase — alguém
    acrescenta à camada 1 uma checagem que abre o Chroma, e o CI passa a falhar
    em todo push por falta de índice em vez de por defeito no código.
    """
    monkeypatch.setattr(rodar, "CATALOGO", tmp_path / "sem-catalogo.sqlite")

    payload = rodar.executar_camadas([1], tamanho_amostra=1)

    assert payload["corpus"] is False
    executadas = [v for v in payload["verificacoes"] if v["executada"]]
    puladas = [v for v in payload["verificacoes"] if not v["executada"]]
    assert executadas and all(v["aprovada"] for v in executadas)
    assert {v["id"] for v in puladas} == {
        "c1.orcamento_real",
        "c1.tools_respondem",
        "c1.citacao_no_contexto",
    }
    assert all(v["motivo"] for v in puladas)
    assert payload["vereditos"]["faithfulness_citacao"] == "nao_medido"


# --- rate limit não é defeito do agente --------------------------------------


def test_provedor_indisponivel_nao_conta_como_falha_do_agente() -> None:
    """Cota estourada é propriedade do ambiente, e a métrica tem de dizer isso.

    Na primeira execução da camada 3 desta fase, 7 de 8 perguntas morreram em
    HTTP 429 do free tier e a única que passou tirou nota cheia. Somar as 7 às
    falhas do agente reprovaria a fase por causa do plano da Groq.
    """
    from copiloto.llm.provedor import ErroDeProvedor

    def sempre_429(pergunta):
        raise ErroDeProvedor("groq respondeu 429")

    resultado = rodar.c3_agente(
        executar=sempre_429,
        juiz=None,
        perguntas=carregar_golden(GOLDEN)[:3],
        pausa=0.0,
    )

    assert resultado.falhas == ()
    assert len(resultado.detalhe["indisponibilidades"]) == 3
    assert resultado.detalhe["medidas"] == 0


def test_pausa_espaca_as_perguntas_e_nao_dorme_antes_da_primeira() -> None:
    """Dormir antes da primeira pergunta só gastaria tempo de parede."""
    dormidas: list[float] = []
    perguntas = carregar_golden(GOLDEN)[:3]

    rodar.c3_agente(
        executar=lambda p: ({"resposta": "", "trechos": []}, rodar.SessaoContadora()),
        juiz=None,
        perguntas=perguntas,
        pausa=25.0,
        dormir=dormidas.append,
    )

    assert dormidas == [25.0, 25.0]


def test_juiz_indisponivel_nao_derruba_a_camada_3() -> None:
    """O juiz é a segunda chamada paga da pergunta — e a que mais bate no limite.

    Sem esta proteção, a primeira recusa do juiz matava a execução inteira e o
    `resultados.json` não era escrito: tudo o que já tinha sido medido se perdia.
    """
    from copiloto.llm.provedor import ErroDeProvedor

    class JuizForaDoAr:
        id_sistema = "eval-judge/camada-3"
        provedor = None

        def julgar(self, pergunta, resposta, evidencias):
            raise ErroDeProvedor("groq respondeu 429")

    resultado = rodar.c3_agente(
        executar=lambda p: ({"resposta": "", "trechos": []}, rodar.SessaoContadora()),
        juiz=JuizForaDoAr(),
        perguntas=carregar_golden(GOLDEN)[:2],
        pausa=0.0,
    )

    assert resultado.falhas == ()
    assert resultado.detalhe["medidas"] == 2
    assert len(resultado.detalhe["indisponibilidades"]) == 2


def test_veredito_do_juiz_e_gravado_com_a_justificativa() -> None:
    """Nota sem justificativa não é revisável — vale para o juiz como para a régua.

    Sem isto, um `faithfulness_juiz` de 0,25 é um número que não dá para
    investigar: não se sabe se o agente errou ou se o juiz errou, e as duas
    conclusões levam a lugares opostos.
    """

    class JuizFixo:
        id_sistema = "eval-judge/camada-3"
        provedor = None

        def julgar(self, pergunta, resposta, evidencias):
            return [
                Nota("", "faithfulness", 0.0, "afirma prazo que não está no trecho"),
                Nota("", "relevancia", 1.0, "responde a pergunta feita"),
            ]

    resultado = rodar.c3_agente(
        executar=lambda p: ({"resposta": "", "trechos": []}, rodar.SessaoContadora()),
        juiz=JuizFixo(),
        perguntas=carregar_golden(GOLDEN)[:1],
        pausa=0.0,
    )

    vereditos = resultado.detalhe["vereditos_do_juiz"]
    assert len(vereditos) == 2
    assert all(v["pergunta"] and v["justificativa"] for v in vereditos)


def test_juiz_recebe_a_evidencia_das_perguntas_de_catalogo() -> None:
    """A regressão do erro que fez `faithfulness_juiz` dar 0,25 sem culpa do agente.

    A primeira versão passava só os `trechos` de `buscar_normativo`. Pergunta de
    catálogo (`tipo == "sql"`) não produz trecho nenhum, então g38 e g39 chegavam
    ao juiz com «(nenhum trecho)» e eram obrigadas a tirar zero — reprovadas por
    citar exatamente a fonte onde a resposta delas mora.
    """
    recebidas: list[list[tuple[str, str]]] = []

    class JuizEspiao:
        id_sistema = "eval-judge/camada-3"
        provedor = None

        def julgar(self, pergunta, resposta, evidencias):
            recebidas.append(list(evidencias))
            return [Nota("", "faithfulness", 1.0, "ok"), Nota("", "relevancia", 1.0, "ok")]

    def executar(pergunta):
        sessao = rodar.SessaoContadora()
        with sessao.no("consultar_catalogo", tipo="retriever") as observacao:
            observacao.atualizar(output='{"normas": [{"numero": "85"}], "total": 1}')
        return {"resposta": "Há uma norma.", "trechos": []}, sessao

    rodar.c3_agente(
        executar=executar,
        juiz=JuizEspiao(),
        perguntas=carregar_golden(GOLDEN, tipo="sql")[:1],
        pausa=0.0,
    )

    (evidencias,) = recebidas
    assert evidencias and evidencias[0][0] == "consultar_catalogo"
    assert "normas" in evidencias[0][1]


def test_evidencia_longa_e_truncada_com_aviso() -> None:
    """O juiz é a segunda chamada paga; evidência inteira o faz estourar o limite."""
    encurtada = rodar._encurtar("x" * (rodar.LIMITE_DE_EVIDENCIA + 500))

    assert len(encurtada) < rodar.LIMITE_DE_EVIDENCIA + 200
    assert "truncada" in encurtada


def test_limitacoes_aparecem_quando_a_amostra_se_perde() -> None:
    """Ressalva calculada, não redigida ao lado do número.

    Sem isto, `faithfulness_juiz` entra no relatório como um número comparável aos
    outros — e ele não é, quando metade da amostra se perdeu no rate limit.
    """
    agente = rodar.Resultado(
        id="c3.agente",
        camada=3,
        descricao="grafo completo",
        total=8,
        detalhe={
            "medidas": 5,
            "indisponibilidades": ["g01: 429", "g21: 413", "g26: 413"],
            "evidencias_truncadas": 2,
        },
    )

    avisos = rodar.limitacoes([agente])

    assert any("3 de 8" in a for a in avisos)
    assert any("truncadas" in a for a in avisos)
    assert any("n=5" in a for a in avisos)


def test_sem_perda_nao_ha_ressalva_a_fazer() -> None:
    """A ressalva some sozinha quando a causa some — é o ponto de calculá-la."""
    agente = rodar.Resultado(
        id="c3.agente",
        camada=3,
        descricao="grafo completo",
        total=12,
        detalhe={"medidas": 12, "indisponibilidades": [], "evidencias_truncadas": 0},
    )

    assert rodar.limitacoes([agente]) == []


# --- reidratação da amostra perdida (Fase 7, retrabalho) ---------------------


def test_recusa_do_provedor_ganha_segunda_chance_apos_a_janela() -> None:
    """Amostra perdida é a maior limitação da camada 3, não o valor das notas.

    O teto da Groq é por minuto: recusa por cota é transitória por definição, e
    descartar a pergunta em vez de repeti-la troca `n` por nada.
    """
    esperas: list[float] = []
    tentativas = iter([ErroDeProvedor("groq recusou o pedido: 429"), "medida"])

    def acao() -> str:
        proxima = next(tentativas)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    reidratacao = Reidratacao(espera=65.0, dormir=esperas.append)

    assert reidratacao.executar(acao) == "medida"
    assert esperas == [65.0], "não esperou a janela inteira antes de repetir"
    assert reidratacao.recuperadas == 1


def test_recusa_persistente_continua_sendo_indisponibilidade() -> None:
    """Repetir não pode virar insistir para sempre: o número tem de doer."""
    esperas: list[float] = []

    def sempre_recusa() -> str:
        raise ErroDeProvedor("groq recusou o pedido: 413")

    reidratacao = Reidratacao(espera=65.0, dormir=esperas.append)

    with pytest.raises(ErroDeProvedor):
        reidratacao.executar(sempre_recusa)
    assert reidratacao.recuperadas == 0
    assert len(esperas) == 1, "esperou mais vezes do que as tentativas permitem"


def test_execucao_sem_recusa_nao_espera_nada() -> None:
    esperas: list[float] = []
    reidratacao = Reidratacao(espera=65.0, dormir=esperas.append)

    assert reidratacao.executar(lambda: "medida") == "medida"
    assert esperas == []
    assert reidratacao.recuperadas == 0


def test_limitacoes_registram_a_recuperacao_sem_esconde_la() -> None:
    """A pergunta foi medida, mas a execução rodou no limite — e isso é dado."""
    resultado = Resultado(
        id="c3.agente",
        camada=3,
        descricao="x",
        total=8,
        detalhe={"medidas": 8, "indisponibilidades": [], "recuperadas_na_segunda_tentativa": 3},
    )

    avisos = limitacoes([resultado])

    assert any("segunda tentativa" in aviso for aviso in avisos)


def test_limitacoes_calam_quando_nada_se_perdeu() -> None:
    """Ressalva que sobrevive à medição que a desmente é propaganda."""
    resultado = Resultado(
        id="c3.agente",
        camada=3,
        descricao="x",
        total=12,
        detalhe={
            "medidas": 12,
            "indisponibilidades": [],
            "recuperadas_na_segunda_tentativa": 0,
            "evidencias_truncadas": 0,
        },
    )

    assert limitacoes([resultado]) == []
