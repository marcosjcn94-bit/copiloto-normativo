"""Chunking pai/filho: rótulo hierárquico, artigo na metadata e artigo citado."""

import pytest

from copiloto.ingestao.chunking import Norma, id_norma_do_chunk, montar_chunks
from copiloto.ingestao.extracao import extrair_blocos

HTML = """
<p>Resolve:</p>
<p>CAP&Iacute;TULO I</p>
<p>DO OBJETO</p>
<p>Art. 1&ordm; Esta Resolu&ccedil;&atilde;o disp&otilde;e sobre seguran&ccedil;a.</p>
<p>Art. 2&ordm; A pol&iacute;tica deve prever:</p>
<p>&sect; 1&ordm; O escopo abrange terceiros.</p>
<p>III - registro das ocorr&ecirc;ncias;</p>
<p>a) com prazo definido;</p>
<p>Art. 3&ordm; A Resolu&ccedil;&atilde;o n&ordm; 4.658 passa a vigorar assim:</p>
<p>"Art. 2&ordm; Redação citada da outra norma."</p>
"""


@pytest.fixture
def norma() -> Norma:
    return Norma(tipo="Resolução CMN", numero="4893", ano=2021)


def test_um_chunk_pai_por_artigo(norma: Norma) -> None:
    pais, _ = montar_chunks(extrair_blocos(HTML), norma)
    assert [p.numero_artigo for p in pais] == [1, 2, 3]
    assert len({p.id for p in pais}) == 3


def test_artigo_citado_de_outra_norma_nao_abre_chunk_pai(norma: Norma) -> None:
    pais, _ = montar_chunks(extrair_blocos(HTML), norma)
    artigo_3 = next(p for p in pais if p.numero_artigo == 3)
    assert "Redação citada da outra norma." in artigo_3.texto


def test_preambulo_nao_vira_chunk(norma: Norma) -> None:
    _, filhos = montar_chunks(extrair_blocos(HTML), norma)
    assert all("Resolve:" not in f.texto for f in filhos)


def test_rotulo_do_filho_e_hierarquico(norma: Norma) -> None:
    _, filhos = montar_chunks(extrair_blocos(HTML), norma)
    rotulos = [f.rotulo for f in filhos]
    assert "Art. 2º, § 1º, III" in rotulos
    assert "Art. 2º, § 1º, III, a)" in rotulos


def test_todo_filho_carrega_norma_e_artigo(norma: Norma) -> None:
    _, filhos = montar_chunks(extrair_blocos(HTML), norma)
    assert filhos
    for filho in filhos:
        assert filho.artigo.startswith("Art. ")
        assert filho.numero_artigo >= 1
        assert filho.norma == "Resolução CMN nº 4893, de 2021"
        assert filho.id_pai in {f"{norma.id_norma}::art-{n}" for n in (1, 2, 3)}


def test_texto_indexavel_leva_caput_e_rotulo(norma: Norma) -> None:
    _, filhos = montar_chunks(extrair_blocos(HTML), norma)
    inciso = next(f for f in filhos if f.rotulo == "Art. 2º, § 1º, III")
    assert "Resolução CMN nº 4893, de 2021 — Art. 2º, § 1º, III" in inciso.texto_indexavel
    assert "A política deve prever:" in inciso.texto_indexavel


def test_redacao_riscada_nao_vira_chunk(norma: Norma) -> None:
    html = "<p><s>Art. 1º Redação revogada.</s></p><p>Art. 1º Redação vigente.</p>"
    pais, filhos = montar_chunks(extrair_blocos(html), norma)
    assert len(pais) == 1
    assert "revogada" not in pais[0].texto
    assert all("revogada" not in f.texto for f in filhos)


def test_status_de_vigencia_e_campo_e_nao_texto() -> None:
    revogada = Norma(tipo="Resolução", numero="4658", ano=2018, revogada=True)
    pais, filhos = montar_chunks(extrair_blocos(HTML), revogada)
    assert all(p.revogada for p in pais)
    assert all(f.revogada for f in filhos)


def test_titulo_do_capitulo_e_absorvido_na_metadata(norma: Norma) -> None:
    pais, filhos = montar_chunks(extrair_blocos(HTML), norma)
    assert pais[0].capitulo == "CAPÍTULO I — DO OBJETO"
    assert all(f.texto != "DO OBJETO" for f in filhos)


def test_id_do_chunk_devolve_a_norma_que_ele_declara(norma: Norma) -> None:
    """Fecha o círculo entre quem cunha o id e quem o lê.

    O filtro por norma no BM25 depende de ler a norma do id, porque o índice
    esparso guarda id e tokens e mais nada. Se o formato do id mudar e esta
    leitura não mudar junto, o filtro não quebra: ele passa a devolver conjunto
    vazio, e a busca escopada fica silenciosamente cega.
    """
    _, filhos = montar_chunks(extrair_blocos(HTML), norma)

    assert filhos, "sem filhos não há o que conferir"
    for filho in filhos:
        assert id_norma_do_chunk(filho.id) == filho.id_norma
        assert id_norma_do_chunk(filho.id_pai) == filho.id_norma
