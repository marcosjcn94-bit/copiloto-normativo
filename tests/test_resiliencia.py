"""Resiliência: backoff, disjuntor e idempotência — sem esperar de verdade."""

import pytest

from copiloto.resiliencia import (
    Disjuntor,
    DisjuntorAberto,
    PoliticaDeRetentativa,
    chave_de_idempotencia,
    com_retentativa,
    esperas,
)

SEM_JITTER = PoliticaDeRetentativa(tentativas=4, espera_inicial=1.0, fator=2.0, jitter=0.0)


class Relogio:
    """Relógio controlado pelo teste."""

    def __init__(self) -> None:
        self.instante = 0.0

    def __call__(self) -> float:
        return self.instante

    def avancar(self, segundos: float) -> None:
        self.instante += segundos


def test_espera_cresce_exponencialmente_e_respeita_o_teto() -> None:
    politica = PoliticaDeRetentativa(
        tentativas=6, espera_inicial=1.0, fator=2.0, jitter=0.0, espera_maxima=8.0
    )
    assert list(esperas(politica)) == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_jitter_mantem_a_espera_dentro_da_faixa() -> None:
    politica = PoliticaDeRetentativa(tentativas=3, espera_inicial=10.0, fator=1.0, jitter=0.5)
    assert list(esperas(politica, aleatorio=lambda: 0.0)) == [5.0, 5.0]
    assert list(esperas(politica, aleatorio=lambda: 1.0)) == [15.0, 15.0]


def test_politica_invalida_e_recusada() -> None:
    with pytest.raises(ValueError):
        PoliticaDeRetentativa(tentativas=0)
    with pytest.raises(ValueError):
        PoliticaDeRetentativa(jitter=2.0)


def test_retentativa_devolve_o_resultado_da_primeira_tentativa_bem_sucedida() -> None:
    chamadas = []

    def operacao() -> str:
        chamadas.append(1)
        if len(chamadas) < 3:
            raise ConnectionError("temporário")
        return "ok"

    esperado: list[float] = []
    assert com_retentativa(operacao, politica=SEM_JITTER, dormir=esperado.append) == "ok"
    assert len(chamadas) == 3
    assert esperado == [1.0, 2.0]


def test_ultima_falha_e_relancada_e_nao_virada_em_resultado_vazio() -> None:
    def sempre_falha() -> str:
        raise TimeoutError("fonte fora do ar")

    with pytest.raises(TimeoutError):
        com_retentativa(sempre_falha, politica=SEM_JITTER, dormir=lambda _: None)


def test_excecao_fora_da_lista_nao_e_retentada() -> None:
    chamadas = []

    def operacao() -> str:
        chamadas.append(1)
        raise ValueError("erro de programação")

    with pytest.raises(ValueError):
        com_retentativa(
            operacao, politica=SEM_JITTER, excecoes=(ConnectionError,), dormir=lambda _: None
        )
    assert len(chamadas) == 1


def test_disjuntor_abre_depois_das_falhas_e_recusa_sem_chamar() -> None:
    relogio = Relogio()
    disjuntor = Disjuntor(falhas_para_abrir=2, janela_de_recuperacao=60.0, agora=relogio)
    chamadas = []

    def falha() -> None:
        chamadas.append(1)
        raise ConnectionError("fonte fora do ar")

    for _ in range(2):
        with pytest.raises(ConnectionError):
            disjuntor.executar(falha)
    assert disjuntor.estado == "aberto"

    with pytest.raises(DisjuntorAberto):
        disjuntor.executar(falha)
    assert len(chamadas) == 2, "com o disjuntor aberto a operação não pode ser chamada"


def test_disjuntor_fica_meio_aberto_e_fecha_com_prova_bem_sucedida() -> None:
    relogio = Relogio()
    disjuntor = Disjuntor(falhas_para_abrir=1, janela_de_recuperacao=30.0, agora=relogio)
    with pytest.raises(ConnectionError):
        disjuntor.executar(lambda: (_ for _ in ()).throw(ConnectionError()))
    assert disjuntor.estado == "aberto"

    relogio.avancar(30.0)
    assert disjuntor.estado == "meio_aberto"
    assert disjuntor.executar(lambda: "ok") == "ok"
    assert disjuntor.estado == "fechado"


def test_disjuntor_aberto_interrompe_a_retentativa() -> None:
    relogio = Relogio()
    disjuntor = Disjuntor(falhas_para_abrir=1, janela_de_recuperacao=60.0, agora=relogio)
    tentativas = []

    def operacao() -> None:
        tentativas.append(1)
        disjuntor.executar(lambda: (_ for _ in ()).throw(ConnectionError()))

    # A retentativa não insiste contra um disjuntor aberto: ela para e deixa o
    # `DisjuntorAberto` subir, que é o sinal de "fonte fora", não de "falhou uma vez".
    with pytest.raises(DisjuntorAberto):
        com_retentativa(operacao, politica=SEM_JITTER, dormir=lambda _: None)
    assert len(tentativas) == 2, "a segunda tentativa encontra o disjuntor aberto e para"


def test_chave_de_idempotencia_e_estavel_e_sensivel_ao_conteudo() -> None:
    chave = chave_de_idempotencia("thread-1", "registrar_crm", {"pergunta": "nuvem"})
    assert chave == chave_de_idempotencia("thread-1", "registrar_crm", {"pergunta": "nuvem"})
    assert chave != chave_de_idempotencia("thread-2", "registrar_crm", {"pergunta": "nuvem"})
    assert len(chave) == 32
