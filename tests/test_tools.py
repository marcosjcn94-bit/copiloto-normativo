"""Fase 4 — contratos estritos, despacho validado e autocorreção de schema.

O teste que dá nome à fase é `test_modelo_corrige_o_schema_na_segunda_tentativa`
e o seu par `test_tres_falhas_seguidas_abortam_para_fallback`: juntos são o
critério de pronto do `fases_de_execuçao.md`. O provedor é roteirizado de
propósito — medir autocorreção contra um modelo real mediria o modelo, não o
mecanismo, e não fecharia duas vezes seguidas com o mesmo resultado.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from copiloto.ingestao.indexacao import ESQUEMA
from copiloto.llm.provedor import ChamadaDeTool, Mensagem, RespostaLLM
from copiloto.recuperacao.retriever import ParametrosRecuperacao, Trecho
from copiloto.resiliencia import PoliticaDeRetentativa
from copiloto.tools import schemas as s
from copiloto.tools.buscar_normativo import buscar_normativo
from copiloto.tools.consultar_catalogo import consultar_catalogo
from copiloto.tools.registrar_crm import ErroDeCrm, enviar_registro, propor_registro
from copiloto.tools.registro import (
    ParametrosDeTools,
    ToolRegistrada,
    carregar_parametros,
    despachar,
    especificacoes,
    executar_com_autocorrecao,
    montar_registro,
)

RAIZ = Path(__file__).resolve().parents[1]
SEM_ESPERA = PoliticaDeRetentativa(tentativas=2, espera_inicial=0.0, jitter=0.0)

PARAMETROS = ParametrosDeTools(
    limite_catalogo=20, fator_sobrebusca=3, timeout_crm=1.0, max_autocorrecao=2
)


# --- duplos de teste ---------------------------------------------------------


class RecuperadorFalso:
    """O mínimo que `buscar_normativo` usa do recuperador da Fase 3."""

    def __init__(self, trechos: list[Trecho], k_final: int = 5) -> None:
        self._trechos = trechos
        self.pedidos: list[int] = []
        self.parametros = ParametrosRecuperacao(
            k_denso=30,
            k_esparso=30,
            k_rrf=60,
            k_final=k_final,
            score_minimo=0.5,
            colecao="teste",
            modelo_embedding="m",
            modelo_rerank="r",
        )

    def buscar(self, pergunta: str, *, k_final: int | None = None, **_: Any) -> list[Trecho]:
        self.pedidos.append(k_final or self.parametros.k_final)
        return self._trechos[: k_final or self.parametros.k_final]


class ProvedorRoteirizado:
    """Devolve respostas pré-escritas, em ordem, e guarda o que recebeu."""

    id_sistema = "roteiro"

    def __init__(self, respostas: list[RespostaLLM]) -> None:
        self._respostas = list(respostas)
        self.chamadas = 0
        self.ultima_transcricao: list[Mensagem] = []

    def gerar(self, mensagens, *, tools=(), temperatura=None) -> RespostaLLM:
        self.chamadas += 1
        self.ultima_transcricao = list(mensagens)
        if not self._respostas:
            raise AssertionError("provedor chamado mais vezes que o roteiro previa")
        return self._respostas.pop(0)


def pedido(nome: str, argumentos: dict[str, Any], id_chamada: str = "c1") -> RespostaLLM:
    return RespostaLLM(
        chamadas=(ChamadaDeTool(id=id_chamada, nome=nome, argumentos=argumentos),),
        id_sistema="roteiro",
    )


def trecho(
    id_: str = "art-1",
    *,
    id_norma: str = "resolucao-bcb-85-2021",
    revogada: bool = False,
    score: float = 0.9,
) -> Trecho:
    return Trecho(
        id=id_,
        id_norma=id_norma,
        norma="Resolução BCB nº 85, de 2021",
        artigo="Art. 7º",
        numero_artigo=7,
        texto="A instituição deve manter política de segurança cibernética.",
        score=score,
        revogada=revogada,
    )


@pytest.fixture
def catalogo() -> sqlite3.Connection:
    """Catálogo em memória com o esquema real da Fase 2, não um arremedo."""
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.executescript(ESQUEMA)
    linhas = [
        (
            "resolucao-bcb-85-2021",
            "Resolução BCB",
            "85",
            2021,
            "2021-03-08",
            "computacao_em_nuvem",
            "vigente",
            "http://x/85",
        ),
        (
            "resolucao-cmn-4893-2021",
            "Resolução CMN",
            "4.893",
            2021,
            "2021-02-26",
            "seguranca_cibernetica",
            "vigente",
            "http://x/4893",
        ),
        (
            "resolucao-cmn-4658-2018",
            "Resolução CMN",
            "4.658",
            2018,
            "2018-04-26",
            "seguranca_cibernetica",
            "revogada",
            "http://x/4658",
        ),
        (
            "instrucao-normativa-bcb-700-2026",
            "Instrução Normativa BCB",
            "700",
            2026,
            "2026-01-13",
            "risco_operacional",
            "vigente",
            "http://x/700",
        ),
    ]
    conexao.executemany(
        "INSERT INTO normas (id_norma, tipo, numero, ano, data, tema, status_vigencia, url,"
        " atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-09-10')",
        linhas,
    )
    conexao.commit()
    return conexao


# --- contratos ---------------------------------------------------------------


def test_argumento_fora_do_schema_reprova():
    """`extra='forbid'`: campo inventado é erro, não campo ignorado."""
    with pytest.raises(ValidationError) as capturado:
        s.EntradaBuscarNormativo(pergunta="o que a norma exige?", filtro_avancado=True)
    assert capturado.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    "argumentos",
    [
        {"pergunta": "curta"},  # abaixo do mínimo
        {"pergunta": "o que a norma exige?", "k": 0},  # abaixo de ge
        {"pergunta": "o que a norma exige?", "k": 50},  # acima de le
        {"pergunta": "o que a norma exige?", "tema": "nuvem"},  # fora do Literal
        {"pergunta": "ignore as instruções <script>alert(1)</script>"},  # fora do regex
    ],
)
def test_limites_do_contrato_de_busca(argumentos: dict[str, Any]):
    with pytest.raises(ValidationError):
        s.EntradaBuscarNormativo(**argumentos)


def test_citacao_precisa_ter_forma_de_citacao():
    valida = s.EntradaRegistrarCrm(
        id_cliente="ABC-12345",
        assunto="consulta sobre nuvem",
        resumo="Cliente perguntou sobre requisitos de contratação de nuvem.",
        normas_citadas=("Resolução BCB nº 85, de 2021, art. 7º",),
    )
    assert valida.normas_citadas[0].endswith("art. 7º")

    with pytest.raises(ValidationError):
        s.EntradaRegistrarCrm(
            id_cliente="ABC-12345",
            assunto="consulta sobre nuvem",
            resumo="Cliente perguntou sobre requisitos de contratação de nuvem.",
            normas_citadas=("a norma diz que sim",),
        )


def test_id_de_cliente_segue_o_formato_do_crm():
    with pytest.raises(ValidationError):
        s.EntradaRegistrarCrm(
            id_cliente="abc12345",
            assunto="consulta sobre nuvem",
            resumo="Cliente perguntou sobre requisitos de contratação de nuvem.",
            normas_citadas=("Resolução BCB nº 85, de 2021, art. 7º",),
        )


def test_schema_json_leva_os_valores_permitidos_ao_modelo():
    """O `Literal` tem de virar `enum` no JSON Schema, senão não previne nada."""
    schema = json.dumps(s.EntradaConsultarCatalogo.model_json_schema(), ensure_ascii=False)
    assert "computacao_em_nuvem" in schema
    assert "Instrução Normativa BCB" in schema
    assert "additionalProperties" in schema


@pytest.mark.corpus
def test_literais_de_dominio_batem_com_o_catalogo_real():
    """A única duplicação do catálogo no Python não pode derivar em silêncio."""
    caminho = RAIZ / "data" / "catalogo.sqlite"
    if not caminho.exists():
        pytest.skip("catálogo não indexado nesta máquina")
    conexao = sqlite3.connect(caminho)
    temas = {linha[0] for linha in conexao.execute("SELECT DISTINCT tema FROM normas")}
    tipos = {linha[0] for linha in conexao.execute("SELECT DISTINCT tipo FROM normas")}
    conexao.close()
    assert temas <= set(s.Tema.__args__)
    assert tipos <= set(s.TipoDeNorma.__args__)


# --- consultar_catalogo ------------------------------------------------------


def test_catalogo_filtra_por_tema_e_exclui_revogada_por_padrao(catalogo):
    saida = consultar_catalogo(
        s.EntradaConsultarCatalogo(tema="seguranca_cibernetica"),
        catalogo=catalogo,
        limite_padrao=20,
    )
    assert saida.total == 1
    assert saida.normas[0].numero == "4.893"


def test_catalogo_devolve_revogada_quando_pedido(catalogo):
    saida = consultar_catalogo(
        s.EntradaConsultarCatalogo(tema="seguranca_cibernetica", status_vigencia="todas"),
        catalogo=catalogo,
        limite_padrao=20,
    )
    assert {n.numero for n in saida.normas} == {"4.893", "4.658"}


def test_catalogo_acha_a_norma_com_ou_sem_ponto_de_milhar(catalogo):
    com_ponto = consultar_catalogo(
        s.EntradaConsultarCatalogo(numero="4.893"), catalogo=catalogo, limite_padrao=20
    )
    sem_ponto = consultar_catalogo(
        s.EntradaConsultarCatalogo(numero="4893"), catalogo=catalogo, limite_padrao=20
    )
    assert com_ponto.normas[0].id_norma == sem_ponto.normas[0].id_norma


def test_catalogo_avisa_quando_truncou(catalogo):
    saida = consultar_catalogo(
        s.EntradaConsultarCatalogo(status_vigencia="todas", limite=2),
        catalogo=catalogo,
        limite_padrao=20,
    )
    assert saida.total == 2
    assert saida.truncado is True


def test_catalogo_ordena_da_mais_nova(catalogo):
    saida = consultar_catalogo(
        s.EntradaConsultarCatalogo(status_vigencia="todas"), catalogo=catalogo, limite_padrao=20
    )
    assert [n.ano for n in saida.normas] == sorted((n.ano for n in saida.normas), reverse=True)


# --- buscar_normativo --------------------------------------------------------


def test_busca_exclui_revogada_por_padrao(catalogo):
    recuperador = RecuperadorFalso([trecho("a"), trecho("b", revogada=True)])
    saida = buscar_normativo(
        s.EntradaBuscarNormativo(pergunta="o que a norma exige sobre nuvem?"),
        recuperador=recuperador,
        catalogo=catalogo,
        fator_sobrebusca=3,
    )
    assert saida.total == 1
    assert saida.ha_revogada is False


def test_busca_marca_quando_ha_revogada(catalogo):
    recuperador = RecuperadorFalso([trecho("b", revogada=True)])
    saida = buscar_normativo(
        s.EntradaBuscarNormativo(pergunta="o que dizia a norma revogada?", apenas_vigentes=False),
        recuperador=recuperador,
        catalogo=catalogo,
        fator_sobrebusca=3,
    )
    assert saida.ha_revogada is True
    assert "(REVOGADA)" in saida.trechos[0].citacao


def test_busca_sobrebusca_quando_ha_filtro(catalogo):
    """Filtrar depois exige pedir mais, senão o filtro derruba o k pedido."""
    recuperador = RecuperadorFalso([trecho(f"a{i}") for i in range(30)])
    buscar_normativo(
        s.EntradaBuscarNormativo(pergunta="requisitos de nuvem?", k=4),
        recuperador=recuperador,
        catalogo=catalogo,
        fator_sobrebusca=3,
    )
    assert recuperador.pedidos == [12]


def test_busca_filtra_por_tema_via_catalogo(catalogo):
    """Tema não está na metadata do chunk: quem resolve é SQL."""
    recuperador = RecuperadorFalso(
        [
            trecho("a", id_norma="resolucao-bcb-85-2021"),
            trecho("b", id_norma="resolucao-cmn-4893-2021"),
        ]
    )
    saida = buscar_normativo(
        s.EntradaBuscarNormativo(pergunta="requisitos de nuvem?", tema="computacao_em_nuvem"),
        recuperador=recuperador,
        catalogo=catalogo,
        fator_sobrebusca=3,
    )
    assert [t.id for t in saida.trechos] == ["a"]


def test_busca_sem_resultado_e_saida_valida(catalogo):
    """Zero trechos é resposta legítima — é o que sustenta o 'não sei' do §8."""
    saida = buscar_normativo(
        s.EntradaBuscarNormativo(pergunta="pergunta sem resposta no corpus?"),
        recuperador=RecuperadorFalso([]),
        catalogo=catalogo,
        fator_sobrebusca=3,
    )
    assert saida.total == 0
    assert saida.trechos == ()


# --- registrar_crm -----------------------------------------------------------


def entrada_crm(
    resumo: str = "Cliente perguntou sobre contratação de nuvem.",
) -> s.EntradaRegistrarCrm:
    return s.EntradaRegistrarCrm(
        id_cliente="ABC-12345",
        assunto="consulta sobre nuvem",
        resumo=resumo,
        normas_citadas=("Resolução BCB nº 85, de 2021, art. 7º",),
    )


def test_chave_de_idempotencia_e_estavel_e_nao_usa_relogio():
    uma = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=2)
    outra = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=2)
    assert uma.idempotency_key == outra.idempotency_key


def test_chave_muda_com_passo_e_com_argumento():
    base = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=2)
    outro_passo = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=3)
    outro_texto = propor_registro(
        entrada_crm("Cliente perguntou sobre continuidade de negócios."),
        thread_id="thread-abc123",
        passo=2,
    )
    assert (
        len({base.idempotency_key, outro_passo.idempotency_key, outro_texto.idempotency_key}) == 3
    )


def test_envio_manda_a_chave_no_header_e_no_corpo():
    capturado: dict = {}

    def handler(requisicao: httpx.Request) -> httpx.Response:
        capturado["chave"] = requisicao.headers["Idempotency-Key"]
        capturado["corpo"] = json.loads(requisicao.content)
        return httpx.Response(201, json={"id": "reg-1"})

    proposta = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=1)
    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    saida = enviar_registro(proposta, cliente=cliente, base_url="http://crm")

    assert capturado["chave"] == proposta.idempotency_key
    assert capturado["corpo"]["idempotency_key"] == proposta.idempotency_key
    assert saida.registrado and saida.id_registro == "reg-1" and saida.duplicado is False


def test_409_e_reenvio_reconhecido_e_nao_falha():
    proposta = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=1)
    cliente = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(409, json={"id": "reg-1"}))
    )
    saida = enviar_registro(proposta, cliente=cliente, base_url="http://crm")
    assert saida.registrado is True
    assert saida.duplicado is True


def test_recusa_do_crm_nao_vira_sucesso_silencioso():
    proposta = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=1)
    cliente = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(422)))
    with pytest.raises(ErroDeCrm):
        enviar_registro(proposta, cliente=cliente, base_url="http://crm")


def test_erro_5xx_do_crm_e_retentado():
    tentativas = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        tentativas["n"] += 1
        return (
            httpx.Response(500) if tentativas["n"] == 1 else httpx.Response(201, json={"id": "r"})
        )

    proposta = propor_registro(entrada_crm(), thread_id="thread-abc123", passo=1)
    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    saida = enviar_registro(proposta, cliente=cliente, base_url="http://crm", politica=SEM_ESPERA)
    assert saida.registrado and tentativas["n"] == 2


# --- registro e despacho -----------------------------------------------------


@pytest.fixture
def registro(catalogo):
    return montar_registro(
        recuperador=RecuperadorFalso([trecho("a")]),
        catalogo=catalogo,
        parametros=PARAMETROS,
        thread_id="thread-abc123",
        passo_atual=lambda: 1,
    )


def test_o_registro_tem_exatamente_as_tres_tools(registro):
    assert sorted(registro) == ["buscar_normativo", "consultar_catalogo", "registrar_crm"]


def test_escrita_e_declarada_e_so_propoe(registro):
    """A tool de escrita não escreve: o executor registrado monta a proposta."""
    assert registro["registrar_crm"].escrita is True
    assert registro["buscar_normativo"].escrita is False

    resultado = despachar(
        ChamadaDeTool(id="c", nome="registrar_crm", argumentos=entrada_crm().model_dump()),
        registro,
    )
    assert isinstance(resultado.saida, s.PropostaDeRegistro)
    assert resultado.saida.idempotency_key


def test_descricao_diz_o_que_a_tool_nao_faz(registro):
    for tool in registro.values():
        assert "NÃO faz" in tool.descricao


def test_especificacoes_saem_em_ordem_estavel(registro):
    assert [e.nome for e in especificacoes(registro)] == sorted(registro)


def test_tool_desconhecida_vira_erro_estruturado(registro):
    resultado = despachar(ChamadaDeTool(id="c", nome="buscar_no_google", argumentos={}), registro)
    assert resultado.erro is not None
    assert resultado.erro.tipo == "tool_desconhecida"
    assert "buscar_normativo" in resultado.erro.problemas[0].permitidos


def test_erro_de_schema_diz_campo_valor_e_permitidos(registro):
    resultado = despachar(
        ChamadaDeTool(
            id="c",
            nome="buscar_normativo",
            argumentos={"pergunta": "requisitos de nuvem?", "tema": "nuvem"},
        ),
        registro,
    )
    corpo = json.loads(resultado.para_modelo())
    assert corpo["erro"] == "schema_invalido"
    problema = corpo["problemas"][0]
    assert problema["campo"] == "tema"
    assert problema["recebido"] == "nuvem"
    assert "computacao_em_nuvem" in problema["permitidos"]


def test_falha_na_execucao_vira_erro_tipado_e_nao_sobe_crua(catalogo):
    def explode(_: Any) -> Any:
        raise sqlite3.OperationalError("banco fora do ar")

    registro = {
        "consultar_catalogo": ToolRegistrada(
            nome="consultar_catalogo",
            descricao="NÃO faz nada",
            entrada=s.EntradaConsultarCatalogo,
            executar=explode,
        )
    }
    resultado = despachar(ChamadaDeTool(id="c", nome="consultar_catalogo", argumentos={}), registro)
    assert resultado.erro is not None
    assert resultado.erro.tipo == "falha_de_execucao"


# --- autocorreção: o critério de pronto da Fase 4 ----------------------------


def test_modelo_corrige_o_schema_na_segunda_tentativa(registro):
    """Argumento inválido → erro estruturado → o modelo acerta na segunda."""
    provedor = ProvedorRoteirizado(
        [
            pedido("buscar_normativo", {"pergunta": "requisitos de nuvem?", "tema": "nuvem"}),
            pedido(
                "buscar_normativo",
                {"pergunta": "requisitos de nuvem?", "tema": "computacao_em_nuvem"},
            ),
        ]
    )
    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("quais os requisitos para contratar nuvem?")],
        registro=registro,
        max_autocorrecao=2,
    )

    assert ciclo.concluiu is True
    assert ciclo.tentativas == 2
    assert provedor.chamadas == 2
    assert isinstance(ciclo.resultados[0].saida, s.SaidaBuscarNormativo)

    # O erro chegou ao modelo como mensagem de tool, e chegou acionável.
    erros = [m for m in ciclo.mensagens if m.papel == "tool" and '"erro"' in m.conteudo]
    assert len(erros) == 1
    assert "computacao_em_nuvem" in erros[0].conteudo


def test_tres_falhas_seguidas_abortam_para_fallback(registro):
    """Com `max_autocorrecao = 2`, a terceira reprovação encerra o ciclo."""
    invalido = {"pergunta": "requisitos de nuvem?", "tema": "nuvem"}
    provedor = ProvedorRoteirizado([pedido("buscar_normativo", invalido) for _ in range(3)])

    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("quais os requisitos para contratar nuvem?")],
        registro=registro,
        max_autocorrecao=2,
    )

    assert ciclo.abortado is True
    assert ciclo.concluiu is False
    assert ciclo.tentativas == 3
    assert provedor.chamadas == 3
    assert "fallback" in ciclo.motivo


def test_argumento_inventado_tambem_dispara_a_correcao(registro):
    """`extra='forbid'` é o que torna a alucinação de parâmetro visível."""
    provedor = ProvedorRoteirizado(
        [
            pedido(
                "consultar_catalogo",
                {"tema": "computacao_em_nuvem", "ordenar_por": "relevancia"},
            ),
            pedido("consultar_catalogo", {"tema": "computacao_em_nuvem"}),
        ]
    )
    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("quais normas de nuvem estão vigentes?")],
        registro=registro,
        max_autocorrecao=2,
    )
    assert ciclo.concluiu is True
    erro = next(m for m in ciclo.mensagens if m.papel == "tool" and '"erro"' in m.conteudo)
    assert json.loads(erro.conteudo)["problemas"][0]["campo"] == "ordenar_por"


def test_falha_de_execucao_nao_gasta_tentativa_de_correcao(catalogo):
    """Argumento válido que quebrou na execução não se conserta reformulando."""

    def explode(_: Any) -> Any:
        raise sqlite3.OperationalError("banco fora do ar")

    registro = {
        "consultar_catalogo": ToolRegistrada(
            nome="consultar_catalogo",
            descricao="NÃO faz nada",
            entrada=s.EntradaConsultarCatalogo,
            executar=explode,
        )
    }
    provedor = ProvedorRoteirizado([pedido("consultar_catalogo", {"tema": "risco_operacional"})])

    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("quais normas de risco operacional?")],
        registro=registro,
        max_autocorrecao=2,
    )
    assert provedor.chamadas == 1
    assert ciclo.abortado is True
    assert ciclo.motivo == "tool falhou na execução"


def test_modelo_que_responde_sem_tool_encerra_o_ciclo(registro):
    provedor = ProvedorRoteirizado([RespostaLLM(conteudo="não encontrei base normativa para isso")])
    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("qual a cotação do dólar hoje?")],
        registro=registro,
        max_autocorrecao=2,
    )
    assert ciclo.concluiu is True
    assert ciclo.resultados == ()
    assert ciclo.resposta is not None and "não encontrei" in ciclo.resposta.conteudo


def test_sem_orcamento_de_correcao_a_primeira_falha_ja_aborta(registro):
    provedor = ProvedorRoteirizado(
        [pedido("buscar_normativo", {"pergunta": "requisitos de nuvem?", "tema": "nuvem"})]
    )
    ciclo = executar_com_autocorrecao(
        provedor,
        [Mensagem.usuario("quais os requisitos para contratar nuvem?")],
        registro=registro,
        max_autocorrecao=0,
    )
    assert ciclo.abortado is True
    assert provedor.chamadas == 1


# --- parâmetros --------------------------------------------------------------


def test_parametros_de_tools_vem_do_toml():
    parametros = carregar_parametros(RAIZ / "config" / "parametros.toml")
    assert parametros.limite_catalogo >= 1
    assert parametros.fator_sobrebusca >= 1
    assert parametros.max_autocorrecao >= 0
