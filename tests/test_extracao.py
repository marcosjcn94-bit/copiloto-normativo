"""Extração: estrutura normativa, encoding e redação riscada."""

from copiloto.ingestao.extracao import (
    acentuacao_intacta,
    classificar_linha,
    extrair_blocos,
    normalizar_texto,
)

HTML_MINIMO = """
<div>
  <p>CAP&Iacute;TULO II</p>
  <p>Se&ccedil;&atilde;o I</p>
  <p>Art. 3&ordm;&nbsp;A pol&iacute;tica deve ser aprovada pelo conselho.</p>
  <p>&sect; 1&ordm; A aprova&ccedil;&atilde;o &eacute; anual.</p>
  <p>III - manter registro das ocorr&ecirc;ncias;</p>
  <p>a) com prazo definido;</p>
</div>
"""


def test_classifica_cada_unidade_normativa() -> None:
    blocos = extrair_blocos(HTML_MINIMO)
    assert [b.tipo for b in blocos] == [
        "capitulo",
        "secao",
        "artigo",
        "paragrafo",
        "inciso",
        "alinea",
    ]


def test_rotulo_e_numero_do_artigo_ficam_na_metadata() -> None:
    artigo = next(b for b in extrair_blocos(HTML_MINIMO) if b.tipo == "artigo")
    assert artigo.rotulo == "Art. 3º"
    assert artigo.numero_artigo == 3


def test_acentuacao_sobrevive_as_entidades_html() -> None:
    textos = " ".join(b.texto for b in extrair_blocos(HTML_MINIMO))
    assert "política" in textos
    assert "Seção" in textos
    assert "aprovação é anual" in textos
    assert acentuacao_intacta(textos)


def test_acentuacao_intacta_reprova_mojibake_e_replacement() -> None:
    assert not acentuacao_intacta("resoluÃ§Ã£o")
    assert not acentuacao_intacta("resolu�ão")
    assert acentuacao_intacta("resolução")


def test_texto_riscado_e_marcado_e_nao_entra_no_texto_vivo() -> None:
    html = "<p><s>Art. 5º Redação anterior.</s></p><p>Art. 5º Redação vigente.</p>"
    blocos = extrair_blocos(html)
    riscados = [b for b in blocos if b.riscado]
    assert len(riscados) == 1
    assert riscados[0].texto == ""
    assert [b.texto for b in blocos if not b.riscado] == ["Art. 5º Redação vigente."]


def test_tachado_parcial_preserva_o_restante_do_dispositivo() -> None:
    html = "<p>Art. 6º O prazo é de <s>trinta</s> sessenta dias.</p>"
    (bloco,) = extrair_blocos(html)
    assert not bloco.riscado
    assert bloco.texto == "Art. 6º O prazo é de sessenta dias."


def test_normalizar_texto_colapsa_espacos_e_nbsp() -> None:
    assert normalizar_texto("  Art.\xa01º   Esta\n\nResolução  ") == "Art. 1º Esta Resolução"


def test_inciso_so_casa_algarismo_romano_valido() -> None:
    assert classificar_linha("IV - fiscalizar;")[0] == "inciso"
    assert classificar_linha("Instituições - devem;")[0] == "outro"


PRE = """<div><pre>
RESOLU&Ccedil;&Atilde;O N&ordm; 3.380

Art. 1&ordm; Determinar &agrave;s institui&ccedil;&otilde;es financeiras a
implementa&ccedil;&atilde;o de estrutura de gerenciamento do risco
operacional.

&sect; 1&ordm; A estrutura deve ser compat&iacute;vel com a natureza da
institui&ccedil;&atilde;o.

I - identifica&ccedil;&atilde;o dos riscos;

Art. 2&ordm; A estrutura deve ser evidenciada em relat&oacute;rio.
</pre></div>"""


def test_normativo_pre_formatado_e_quebrado_por_dispositivo() -> None:
    blocos = extrair_blocos(PRE)
    assert [b.tipo for b in blocos if b.tipo != "outro"] == [
        "artigo",
        "paragrafo",
        "inciso",
        "artigo",
    ]


def test_quebra_de_largura_fixa_nao_fragmenta_o_dispositivo() -> None:
    artigo = next(b for b in extrair_blocos(PRE) if b.tipo == "artigo")
    assert artigo.texto.endswith("risco operacional.")
    assert "institui" in artigo.texto and "  " not in artigo.texto


def test_documento_com_p_nao_sofre_rejuncao() -> None:
    html = "<p>Art. 1º Objeto.</p><p>Considerando o disposto na lei.</p><p>Art. 2º Prazo.</p>"
    assert [b.tipo for b in extrair_blocos(html)] == ["artigo", "outro", "artigo"]
