"""O trace da Fase 7: o que ele carrega, e o que ele nunca pode derrubar.

Nenhum destes testes fala com o Langfuse. O que se prova aqui é o contrato do
`tracing.py` contra um cliente de mentira — quais atributos saem, em que
observação, e o que acontece quando o backend falha. A ida real ao Langfuse Cloud
é verificação manual, e está anotada no relatório da fase: teste que depende de
serviço de terceiro no ar não é teste, é monitor.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from copiloto.llm.provedor import Mensagem, RespostaLLM, Uso
from copiloto.observabilidade.tracing import (
    FICHA_NAO_DECLARADA,
    ProvedorRastreado,
    RastreadorLangfuse,
    RastreadorNulo,
    abrir_rastreador,
    autenticar,
    sessao_nula,
    versao_da_ficha,
)

RAIZ = Path(__file__).resolve().parents[1]


# --- duplos ------------------------------------------------------------------


class SpanFalso:
    """Guarda tudo que recebeu em `update`, na ordem."""

    def __init__(self, nome: str, tipo: str, **abertura: Any) -> None:
        self.nome = nome
        self.tipo = tipo
        self.abertura = abertura
        self.atualizacoes: list[dict[str, Any]] = []

    def update(self, **campos: Any) -> None:
        self.atualizacoes.append(campos)

    @property
    def tudo(self) -> dict[str, Any]:
        junto: dict[str, Any] = dict(self.abertura)
        for atualizacao in self.atualizacoes:
            junto.update(atualizacao)
        return junto


class ClienteFalso:
    def __init__(self) -> None:
        self.spans: list[SpanFalso] = []
        self.descargas = 0

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str = "span", **extras: Any):
        span = SpanFalso(name, as_type, **extras)
        self.spans.append(span)
        yield span

    def flush(self) -> None:
        self.descargas += 1

    def de(self, nome: str) -> SpanFalso:
        return next(s for s in self.spans if s.nome == nome)


class PropagacaoFalsa:
    """O `propagate_attributes` do SDK, guardando o que foi carimbado no trace."""

    def __init__(self) -> None:
        self.atributos: list[dict[str, Any]] = []

    @contextmanager
    def __call__(self, **campos: Any):
        self.atributos.append(campos)
        yield


class ProvedorFalso:
    id_sistema = "groq"

    def __init__(self, resposta: RespostaLLM) -> None:
        self.resposta = resposta

    def gerar(self, mensagens, *, tools=(), temperatura=None) -> RespostaLLM:
        return self.resposta


@pytest.fixture
def rastreador() -> tuple[RastreadorLangfuse, ClienteFalso, PropagacaoFalsa]:
    cliente = ClienteFalso()
    propagar = PropagacaoFalsa()
    return (
        RastreadorLangfuse(cliente, versao_ficha="1.2.3", propagar=propagar),
        cliente,
        propagar,
    )


# --- as duas linhas que a Fase 9 consome -------------------------------------


def test_trace_carrega_id_sistema_e_versao_ficha(rastreador) -> None:
    """O critério de pronto da fase, em forma de asserção.

    Sem estes dois atributos o custo agregado não pode ser atribuído por sistema,
    e recuperá-los depois exigiria reprocessar trace — que é o mesmo que não ter.
    """
    tracer, _, propagar = rastreador
    with tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq"):
        pass

    (atributos,) = propagar.atributos
    assert atributos["metadata"] == {"id_sistema": "groq", "versao_ficha": "1.2.3"}
    assert "id_sistema:groq" in atributos["tags"]
    assert "versao_ficha:1.2.3" in atributos["tags"]


def test_thread_id_vira_session_id(rastreador) -> None:
    """É o `thread_id` que costura `/perguntar` e `/aprovar` no painel."""
    tracer, _, propagar = rastreador
    with tracer.sessao(nome="aprovar", thread_id="thread-0001", id_sistema="groq"):
        pass
    assert propagar.atributos[0]["session_id"] == "thread-0001"


def test_versao_ficha_sem_governanca_yaml_e_declarada_ausente(tmp_path: Path) -> None:
    """Enquanto a ficha da Fase 9 não existe, o trace diz que ela não existe.

    Inventar `1.0.0` faria o inventário afirmar que há ficha declarada quando não
    há — o oposto do que a camada de governança serve para provar.
    """
    assert versao_da_ficha(tmp_path) == FICHA_NAO_DECLARADA


def test_versao_ficha_le_o_yaml_quando_ele_existe(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "governanca.yaml").write_text(
        "identificacao:\n  id: copiloto\n  versao: 2.1.0\nproposito:\n  versao: 9.9.9\n",
        encoding="utf-8",
    )
    # A `versao` de `proposito` não pode vencer: a leitura é do bloco certo.
    assert versao_da_ficha(tmp_path) == "2.1.0"


# --- geração: tokens e modelo ------------------------------------------------


def test_provedor_rastreado_emite_tokens_no_formato_que_gera_custo(rastreador) -> None:
    """`input`/`output`/`total` são as chaves que o Langfuse casa com o preço.

    Renomeá-las para `tokens_entrada` deixaria o painel com token e sem custo —
    e o custo por consulta é insumo do §16.6.
    """
    tracer, cliente, _ = rastreador
    resposta = RespostaLLM(
        conteudo="ok",
        modelo="llama-3.3-70b-versatile",
        id_sistema="groq",
        uso=Uso(tokens_entrada=1200, tokens_saida=300),
    )
    with tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq") as sessao:
        envolvido = ProvedorRastreado(ProvedorFalso(resposta), sessao=sessao)
        assert envolvido.gerar([Mensagem.usuario("oi")]) is resposta

    geracao = cliente.de("llm.gerar")
    assert geracao.tipo == "generation"
    assert geracao.tudo["usage_details"] == {"input": 1200, "output": 300, "total": 1500}
    assert geracao.tudo["model"] == "llama-3.3-70b-versatile"


def test_provedor_rastreado_preserva_o_contrato_do_protocol() -> None:
    """Quem embrulha não pode mudar a identidade de quem foi embrulhado."""
    provedor = ProvedorFalso(RespostaLLM(conteudo="ok"))
    assert ProvedorRastreado(provedor).id_sistema == provedor.id_sistema


# --- o que nunca pode acontecer ----------------------------------------------


class ClienteQuebrado:
    def start_as_current_observation(self, **_: Any):
        raise RuntimeError("langfuse fora do ar")

    def flush(self) -> None:
        raise RuntimeError("langfuse fora do ar")


def test_backend_quebrado_nao_derruba_a_execucao() -> None:
    """A regra dura do módulo: observabilidade não pode virar indisponibilidade."""
    tracer = RastreadorLangfuse(ClienteQuebrado(), versao_ficha="1.0.0", propagar=PropagacaoFalsa())
    with tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq") as sessao:
        with sessao.no("deliberar") as observacao:
            observacao.atualizar(output={"passo": 1})
        sessao.registrar(input={"pergunta": "x"})
    tracer.descarregar()  # também não pode levantar


def test_sem_chaves_o_rastreador_e_nulo_e_diz_o_motivo() -> None:
    """Ambiente sem chave é operação normal, não erro de configuração."""
    tracer = abrir_rastreador({}, raiz=RAIZ)
    assert isinstance(tracer, RastreadorNulo)
    assert tracer.ativo is False
    assert tracer.motivo == "chaves_ausentes"


def test_rastreador_nulo_atende_a_superficie_inteira() -> None:
    """Se o nulo não aceitar uma chamada que o real aceita, o dev sem chave quebra."""
    tracer = RastreadorNulo()
    with tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq") as sessao:
        with sessao.no("sanear") as observacao:
            observacao.atualizar(output={"passo": 1}, level="ERROR")
        with sessao.geracao(nome="llm.gerar", modelo="m", id_sistema="groq") as geracao:
            geracao.atualizar(usage_details={"input": 1, "output": 1, "total": 2})
        sessao.registrar(input={}, output={})
    tracer.descarregar()


def test_sessao_nula_serve_de_padrao_no_grafo() -> None:
    """`Dependencias` monta sem falar de observabilidade — é o que os testes fazem."""
    with sessao_nula().no("deliberar") as observacao:
        observacao.atualizar(output={})


# --- credencial errada não pode passar por rastreador ativo ------------------


class ClienteSemCredencial:
    """O SDK não autentica ao construir: quem recusa é o `auth_check`."""

    def auth_check(self) -> bool:
        raise RuntimeError("401 Invalid credentials")


class ClienteQueDizNao:
    def auth_check(self) -> bool:
        return False


def test_credencial_recusada_vira_rastreador_nulo_com_motivo() -> None:
    """A lacuna que a verificação manual da Fase 7 expôs.

    Com chave errada o cliente sobe, engole todo span e nunca reclama. Sem esta
    checagem o `/saude` responderia `observabilidade.ativo = true` num serviço
    que não exporta nada — o modo de falha de observabilidade que importa é
    justamente o silencioso.
    """
    tracer = autenticar(ClienteSemCredencial(), PropagacaoFalsa(), raiz=RAIZ)

    assert isinstance(tracer, RastreadorNulo)
    assert tracer.ativo is False
    assert tracer.motivo == "credenciais_invalidas"


def test_auth_check_falso_tambem_reprova() -> None:
    """Nem toda recusa levanta: o SDK pode simplesmente devolver `False`."""
    tracer = autenticar(ClienteQueDizNao(), PropagacaoFalsa(), raiz=RAIZ)

    assert isinstance(tracer, RastreadorNulo)
    assert tracer.motivo == "credenciais_invalidas"


def test_credencial_boa_devolve_rastreador_ativo() -> None:
    cliente = ClienteFalso()
    cliente.auth_check = lambda: True  # type: ignore[method-assign]

    tracer = autenticar(cliente, PropagacaoFalsa(), raiz=RAIZ)

    assert isinstance(tracer, RastreadorLangfuse)
    assert tracer.ativo is True


# --- tipo de observação, e por que ele não é enfeite -------------------------


def test_cada_no_recebe_o_tipo_que_descreve_o_que_ele_faz(rastreador) -> None:
    """Tipo errado custa recurso concreto no painel, não estética.

    `guardrail` é o que permite filtrar quanto o validador de saída reprova;
    `agent` é o que faz `deliberar` virar nó do agent graph; `tool` é o que um
    avaliador LLM-as-a-judge usa para mirar só as chamadas de tool. Tudo marcado
    como `span` joga as três coisas fora.
    """
    from copiloto.grafo.grafo import TIPO_DO_NO

    tracer, cliente, _ = rastreador
    with tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq") as sessao:
        for nome, tipo in TIPO_DO_NO.items():
            with sessao.no(nome, tipo=tipo):
                pass

    tipos = {s.nome: s.tipo for s in cliente.spans}
    assert tipos["sanear"] == "guardrail"
    assert tipos["verificar"] == "guardrail"
    assert tipos["deliberar"] == "agent"
    assert tipos["escrever"] == "tool"


def test_todo_no_do_grafo_tem_tipo_declarado() -> None:
    """Nó novo sem tipo cai no `span` genérico — este teste avisa antes."""
    from copiloto.grafo.grafo import TIPO_DO_NO

    esperados = {"sanear", "deliberar", "verificar", "aprovar", "escrever", "recusar"}
    assert set(TIPO_DO_NO) == esperados


# --- formato de mensagem que o painel renderiza ------------------------------


def test_mensagem_vira_formato_openai() -> None:
    """`papel`/`conteudo` viram `role`/`content` — senão o painel mostra JSON cru."""
    from copiloto.observabilidade.tracing import mensagem_para_openai

    assert mensagem_para_openai(Mensagem.usuario("oi")) == {"role": "user", "content": "oi"}


def test_chamada_de_tool_sai_no_formato_que_vira_cartao() -> None:
    """`arguments` como string JSON: é o que o Langfuse reconhece como chamada."""
    from copiloto.llm.provedor import ChamadaDeTool
    from copiloto.observabilidade.tracing import mensagem_para_openai

    chamada_bruta = ChamadaDeTool(id="c1", nome="buscar_normativo", argumentos={"k": 5})
    corpo = mensagem_para_openai(Mensagem.assistente("", [chamada_bruta]))

    (chamada,) = corpo["tool_calls"]
    assert chamada["type"] == "function"
    assert chamada["function"]["name"] == "buscar_normativo"
    assert chamada["function"]["arguments"] == '{"k": 5}'


def test_resultado_de_tool_carrega_o_id_da_chamada() -> None:
    from copiloto.observabilidade.tracing import mensagem_para_openai

    corpo = mensagem_para_openai(Mensagem.resultado_de_tool("c1", "{}"))

    assert corpo["role"] == "tool"
    assert corpo["tool_call_id"] == "c1"


# --- uma observação por chamada de tool --------------------------------------


class _RegistroDeSpans:
    """Sessão mínima que só anota nome e tipo de cada observação aberta."""

    def __init__(self) -> None:
        self.abertos: list[tuple[str, str]] = []

    @contextmanager
    def no(self, nome: str, *, tipo: str = "span"):
        self.abertos.append((nome, tipo))
        yield _ObservacaoMuda()

    @contextmanager
    def geracao(self, *, nome: str, modelo: str, id_sistema: str):
        self.abertos.append((nome, "generation"))
        yield _ObservacaoMuda()

    def registrar(self, **campos: Any) -> None:
        return None


class _ObservacaoMuda:
    def atualizar(self, **campos: Any) -> None:
        return None


def _registro_de_tools():
    from pydantic import BaseModel, ConfigDict

    from copiloto.tools.registro import ToolRegistrada

    class Entrada(BaseModel):
        model_config = ConfigDict(extra="forbid")
        q: str = ""

    class Saida(BaseModel):
        ok: bool = True

    return {
        "buscar_normativo": ToolRegistrada(
            nome="buscar_normativo",
            descricao="lê",
            entrada=Entrada,
            executar=lambda _: Saida(),
        ),
        "registrar_crm": ToolRegistrada(
            nome="registrar_crm",
            descricao="propõe escrita",
            entrada=Entrada,
            executar=lambda _: Saida(),
            escrita=True,
        ),
    }


def test_cada_chamada_de_tool_vira_uma_observacao_do_tipo_certo() -> None:
    """Leitura é `retriever`, escrita é `tool` — e o tipo sai do contrato da tool.

    Um span único em volta do laço daria só o total. O que se leva a um trace é
    *qual* chamada demorou ou reprovou, e isso exige uma observação por despacho,
    irmã da geração que a pediu.
    """
    from copiloto.llm.provedor import ChamadaDeTool
    from copiloto.tools.registro import executar_com_autocorrecao

    class ProvedorComTools:
        id_sistema = "groq"
        chamou = False

        def gerar(self, mensagens, *, tools=(), temperatura=None):
            if self.chamou:
                return RespostaLLM(conteudo="pronto")
            self.chamou = True
            return RespostaLLM(
                chamadas=(
                    ChamadaDeTool(id="c1", nome="buscar_normativo", argumentos={"q": "x"}),
                    ChamadaDeTool(id="c2", nome="registrar_crm", argumentos={"q": "y"}),
                )
            )

    sessao = _RegistroDeSpans()
    executar_com_autocorrecao(
        ProvedorComTools(),
        [Mensagem.usuario("oi")],
        registro=_registro_de_tools(),
        max_autocorrecao=1,
        sessao=sessao,
    )

    assert ("buscar_normativo", "retriever") in sessao.abertos
    assert ("registrar_crm", "tool") in sessao.abertos


def test_despacho_sem_sessao_continua_funcionando() -> None:
    """`sessao=None` é o modo de quase todo teste — e do CI, sem o extra `[obs]`."""
    from copiloto.llm.provedor import ChamadaDeTool
    from copiloto.tools.registro import executar_com_autocorrecao

    class ProvedorUmaTool:
        id_sistema = "groq"
        chamou = False

        def gerar(self, mensagens, *, tools=(), temperatura=None):
            if self.chamou:
                return RespostaLLM(conteudo="pronto")
            self.chamou = True
            return RespostaLLM(
                chamadas=(ChamadaDeTool(id="c1", nome="buscar_normativo", argumentos={"q": "x"}),)
            )

    ciclo = executar_com_autocorrecao(
        ProvedorUmaTool(),
        [Mensagem.usuario("oi")],
        registro=_registro_de_tools(),
        max_autocorrecao=1,
    )

    assert [r.tool for r in ciclo.resultados] == ["buscar_normativo"]


def test_o_trace_e_descarregado_no_desligamento_e_nao_a_cada_resposta() -> None:
    """`flush()` por requisição anula o lote e cobra rede de quem perguntou.

    O SDK envia em lote num thread de fundo. Descarregar por resposta poria uma
    ida à rede no caminho de toda consulta — latência paga pelo usuário para
    beneficiar o painel. No desligamento é o contrário: sem descarregar, a fila
    morre com o processo, e no scale-to-zero da Fase 8 o processo morre sempre.
    """
    from copiloto.api.main import Servico

    cliente = ClienteFalso()
    cliente.auth_check = lambda: True  # type: ignore[method-assign]
    tracer = autenticar(cliente, PropagacaoFalsa(), raiz=RAIZ)

    servico = Servico(
        provedor=ProvedorFalso(RespostaLLM(conteudo="ok")),
        recuperador=None,  # type: ignore[arg-type]
        catalogo=None,  # type: ignore[arg-type]
        parametros_grafo=None,  # type: ignore[arg-type]
        parametros_tools=None,  # type: ignore[arg-type]
        checkpointer=None,
        caminho_checkpoint=RAIZ / "data" / "checkpoints.sqlite",
        rastreador=tracer,
    )

    assert cliente.descargas == 0
    servico.fechar()
    assert cliente.descargas == 1


def test_erro_dentro_da_sessao_sobe_intacto(rastreador) -> None:
    """O instrumentador não pode trocar o erro do copiloto pelo erro dele mesmo.

    Um `except Exception` em volta do `yield` de um `@contextmanager` engole a
    exceção que o contextlib lança de volta, o gerador cede duas vezes e o Python
    levanta `RuntimeError: generator didn't stop after throw()`. Foi assim que um
    HTTP 413 da Groq chegou disfarçado de defeito do tracing ao gerar o primeiro
    trace desta fase.
    """
    tracer, _, _ = rastreador

    class ErroDoCopiloto(RuntimeError):
        pass

    with (
        pytest.raises(ErroDoCopiloto),
        tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq"),
    ):
        raise ErroDoCopiloto("groq recusou o pedido: 413")


def test_erro_dentro_de_um_no_sobe_intacto(rastreador) -> None:
    """Mesma regra um nível abaixo, no span de nó."""
    tracer, _, _ = rastreador

    with (
        pytest.raises(ZeroDivisionError),
        tracer.sessao(nome="perguntar", thread_id="abc12345", id_sistema="groq") as sessao,
        sessao.no("deliberar", tipo="agent"),
    ):
        _ = 1 / 0
