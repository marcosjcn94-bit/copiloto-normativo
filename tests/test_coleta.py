"""Coleta: leitura do corpus, idempotência e comportamento sob falha."""

from datetime import date
from pathlib import Path

import pytest

from copiloto.ingestao.coleta import (
    Corpus,
    NormaDoCorpus,
    carregar_corpus,
    coletar,
)
from copiloto.resiliencia import Disjuntor, PoliticaDeRetentativa

CORPUS_REAL = Path(__file__).resolve().parents[1] / "config" / "corpus.toml"
SEM_ESPERA = PoliticaDeRetentativa(tentativas=3, espera_inicial=0.0, jitter=0.0)


def norma_falsa(numero: str = "4893", status: str = "vigente") -> NormaDoCorpus:
    return NormaDoCorpus(
        tipo="Resolução CMN",
        numero=numero,
        ano=2021,
        data=date(2021, 2, 26),
        tema="seguranca_cibernetica",
        status_vigencia=status,
        url="https://exemplo/norma",
    )


def registro(numero: str = "4893", revogado: bool = False, ano: int = 2021) -> dict:
    return {
        "Titulo": f"Resolução Nº {numero}",
        "Tipo": "Resolução CMN",
        "Numero": float(numero),
        "Data": f"{ano}-02-26T00:00:00Z",
        "Revogado": revogado,
        "Assunto": "Dispõe sobre a política de segurança cibernética.",
        "Texto": "<p>Art. 1º Esta Resolução dispõe sobre segurança cibernética.</p>",
    }


@pytest.fixture
def corpus() -> Corpus:
    return Corpus(
        endpoint_normativo="https://exemplo/normativo",
        endpoint_busca="https://exemplo/busca",
        normas=[norma_falsa()],
    )


def test_corpus_real_carrega_e_tem_o_tamanho_do_briefing() -> None:
    carregado = carregar_corpus(CORPUS_REAL)
    assert 20 <= len(carregado.normas) <= 40, "o briefing fixa o corpus entre 20 e 40 normativos"
    assert len({n.id_norma for n in carregado.normas}) == len(carregado.normas)


def test_corpus_real_tem_ao_menos_uma_norma_revogada() -> None:
    carregado = carregar_corpus(CORPUS_REAL)
    revogadas = [n for n in carregado.normas if n.status_vigencia == "revogada"]
    assert revogadas, "sem norma revogada o campo de vigência não é exercitado"


def test_status_invalido_no_corpus_e_erro_de_curadoria(tmp_path: Path) -> None:
    arquivo = tmp_path / "corpus.toml"
    arquivo.write_text(
        '[fonte]\nendpoint_normativo = "u"\nendpoint_busca = "b"\n\n'
        '[[norma]]\ntipo = "Resolução"\nnumero = "1"\nano = 2020\ndata = 2020-01-01\n'
        'tema = "t"\nstatus_vigencia = "talvez"\nurl = "u"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="status_vigencia"):
        carregar_corpus(arquivo)


def test_coleta_baixa_e_grava_o_bruto(corpus: Corpus, tmp_path: Path) -> None:
    relatorio = coletar(corpus, tmp_path, buscar=lambda *_: [registro()])
    assert relatorio.baixadas == ["resolucao-cmn-4893-2021"]
    assert (tmp_path / "resolucao-cmn-4893-2021.json").exists()


def test_reexecutar_nao_rebaixa_nada(corpus: Corpus, tmp_path: Path) -> None:
    chamadas = []

    def buscar(*_):
        chamadas.append(1)
        return [registro()]

    coletar(corpus, tmp_path, buscar=buscar)
    segunda = coletar(corpus, tmp_path, buscar=buscar)
    assert len(chamadas) == 1, "a segunda execução não pode bater na fonte de novo"
    assert segunda.reaproveitadas == ["resolucao-cmn-4893-2021"]
    assert segunda.total_em_disco == 1


def test_forcar_rebaixa_de_proposito(corpus: Corpus, tmp_path: Path) -> None:
    coletar(corpus, tmp_path, buscar=lambda *_: [registro()])
    segunda = coletar(corpus, tmp_path, buscar=lambda *_: [registro()], forcar=True)
    assert segunda.baixadas == ["resolucao-cmn-4893-2021"]


def test_divergencia_de_vigencia_e_relatada_e_nao_silenciada(tmp_path: Path) -> None:
    corpus = Corpus(
        endpoint_normativo="u",
        endpoint_busca="b",
        normas=[norma_falsa(status="vigente")],
    )
    relatorio = coletar(corpus, tmp_path, buscar=lambda *_: [registro(revogado=True)])
    assert "resolucao-cmn-4893-2021" in relatorio.divergencias_de_vigencia
    assert "coletado=revogada" in relatorio.divergencias_de_vigencia["resolucao-cmn-4893-2021"]


def test_falha_de_uma_norma_nao_derruba_o_corpus(tmp_path: Path) -> None:
    corpus = Corpus(
        endpoint_normativo="u",
        endpoint_busca="b",
        normas=[norma_falsa("4893"), norma_falsa("4658")],
    )

    def buscar(_endpoint, parametros):
        if parametros["p2"] == "4893":
            raise ConnectionError("timeout")
        return [registro("4658")]

    relatorio = coletar(
        corpus,
        tmp_path,
        buscar=buscar,
        politica=SEM_ESPERA,
        disjuntor=Disjuntor(falhas_para_abrir=99),
        dormir=lambda _: None,
    )
    assert "resolucao-cmn-4893-2021" in relatorio.falhas
    assert relatorio.baixadas == ["resolucao-cmn-4658-2021"]


def test_conteudo_vazio_e_falha_e_nao_arquivo_vazio(corpus: Corpus, tmp_path: Path) -> None:
    relatorio = coletar(corpus, tmp_path, buscar=lambda *_: [])
    assert relatorio.falhas
    assert not list(tmp_path.glob("*.json"))


def test_versao_e_escolhida_pelo_ano_declarado(corpus: Corpus, tmp_path: Path) -> None:
    import json

    coletar(
        corpus,
        tmp_path,
        buscar=lambda *_: [registro(ano=1998), registro(ano=2021)],
    )
    salvo = json.loads((tmp_path / "resolucao-cmn-4893-2021.json").read_text(encoding="utf-8"))
    assert salvo["Data"].startswith("2021")


# --- contrato da fonte (pendência 1 do §15) ----------------------------------
#
# A URL do BCB não foi cravada de memória: ela está em `config/corpus.toml` e os
# dois testes abaixo batem na fonte real para provar que o formato continua o
# mesmo. São `rede` e ficam fora do CI — o valor deles é rodar antes de uma
# recoleta, quando a pergunta "a fonte mudou?" precisa de resposta e não de
# suposição. Se o BCB mudar o contrato, é aqui que aparece, e não em corpus meio
# baixado.


@pytest.mark.rede
def test_endpoint_de_normativo_do_bcb_mantem_o_contrato() -> None:
    from copiloto.ingestao.coleta import buscar_no_bcb

    corpus = carregar_corpus(CORPUS_REAL)
    alvo = next(n for n in corpus.normas if n.status_vigencia == "revogada")

    conteudo = buscar_no_bcb(timeout=30.0)(
        corpus.endpoint_normativo, {"p1": alvo.tipo, "p2": alvo.numero}
    )

    assert conteudo, "a fonte devolveu lista vazia para uma norma do corpus"
    registro_real = conteudo[0]
    assert {"Texto", "Revogado", "Data", "Numero", "Tipo"} <= set(registro_real)
    assert registro_real["Revogado"] is True, "vigência é campo da fonte, não texto"


@pytest.mark.rede
def test_endpoint_de_busca_exige_paginacao_explicita() -> None:
    """`startrow` e `rowlimit` não são opcionais: sem eles a fonte responde 500.

    Está aqui porque é a pegadinha da curadoria — o endpoint de busca parece
    aceitar só `querytext`, e o erro volta como página HTML em latin-1, que
    quebra no decode antes de virar HTTP de erro legível.
    """
    import httpx

    corpus = carregar_corpus(CORPUS_REAL)

    completa = httpx.get(
        corpus.endpoint_busca,
        params={"querytext": "computação em nuvem", "startrow": 0, "rowlimit": 3},
        timeout=30.0,
    )
    assert completa.status_code == 200
    corpo = completa.json()
    assert corpo["TotalRows"] > 0
    assert {"title", "listItemId"} <= set(corpo["Rows"][0])

    incompleta = httpx.get(
        corpus.endpoint_busca, params={"querytext": "computação em nuvem"}, timeout=30.0
    )
    assert incompleta.status_code == 500
