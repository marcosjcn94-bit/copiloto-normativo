"""Assinatura do encoder denso: o índice tem de ter sido gerado por quem consulta.

O `fastembed` já trocou o pooling do mesmo modelo entre versões (0.5 usava CLS,
0.8 usa mean). Quando isso acontece, vetor indexado e vetor de consulta passam a
viver em espaços diferentes: nada quebra, nenhum teste fica vermelho, e a
recuperação só piora. Estes testes existem para transformar essa degradação
silenciosa em erro alto.
"""

from pathlib import Path

import pytest

from copiloto.assinatura import (
    NOME_DO_ARQUIVO,
    IndiceDesatualizado,
    assinatura_atual,
    conferir_assinatura,
    gravar_assinatura,
)

MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_assinatura_atual_traz_modelo_e_versao_do_fastembed():
    atual = assinatura_atual(MODELO)
    assert atual["modelo"] == MODELO
    assert atual["fastembed"]


def test_assinatura_recem_gravada_confere(tmp_path: Path):
    caminho = tmp_path / "assinatura_densa.json"
    gravar_assinatura(caminho, modelo=MODELO)
    conferir_assinatura(caminho, modelo=MODELO)


def test_versao_diferente_de_fastembed_falha_alto(tmp_path: Path):
    caminho = tmp_path / "assinatura_densa.json"
    gravar_assinatura(caminho, modelo=MODELO)
    caminho.write_text(
        caminho.read_text(encoding="utf-8").replace(assinatura_atual(MODELO)["fastembed"], "0.5.1"),
        encoding="utf-8",
    )
    with pytest.raises(IndiceDesatualizado, match="fastembed"):
        conferir_assinatura(caminho, modelo=MODELO)


def test_modelo_diferente_falha_alto(tmp_path: Path):
    caminho = tmp_path / "assinatura_densa.json"
    gravar_assinatura(caminho, modelo=MODELO)
    with pytest.raises(IndiceDesatualizado, match="modelo"):
        conferir_assinatura(caminho, modelo="BAAI/bge-small-en-v1.5")


def test_assinatura_ausente_avisa_mas_nao_quebra(tmp_path: Path, caplog):
    # Índice gerado antes desta checagem existir: não dá para afirmar que está
    # errado, então avisa e segue. Falhar aqui inutilizaria índices legítimos.
    conferir_assinatura(tmp_path / "nao_existe.json", modelo=MODELO)
    assert "assinatura" in caplog.text.lower()


# --- fiação: quem grava e quem confere ---------------------------------------


class _Vetor:
    def tolist(self) -> list[float]:
        return [0.1, 0.2]


class EmbedderFalso:
    def embed(self, textos):
        return [_Vetor() for _ in textos]


class ColecaoFalsa:
    def upsert(self, **kwargs) -> None:
        pass


def test_indexacao_densa_deixa_a_assinatura_ao_lado_do_indice(tmp_path: Path):
    from tests.test_indexacao import gravar_bruto, norma

    from copiloto.ingestao.indexacao import abrir_catalogo, indexar

    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    caminho = tmp_path / "indices" / "assinatura_densa.json"
    conexao = abrir_catalogo(tmp_path / "catalogo.sqlite")
    try:
        indexar(
            [n],
            bruto=bruto,
            conexao=conexao,
            colecao=ColecaoFalsa(),
            embedder=EmbedderFalso(),
            modelo_embedding=MODELO,
            caminho_assinatura=caminho,
        )
    finally:
        conexao.close()

    conferir_assinatura(caminho, modelo=MODELO)


def test_indexacao_sem_denso_nao_grava_assinatura(tmp_path: Path):
    # Sem vetor gravado não há espaço vetorial a assinar; um arquivo aqui
    # afirmaria algo sobre um índice denso que esta execução não tocou.
    from tests.test_indexacao import gravar_bruto, norma

    from copiloto.ingestao.indexacao import abrir_catalogo, indexar

    bruto = tmp_path / "bruto"
    n = norma()
    gravar_bruto(bruto, n)
    caminho = tmp_path / "indices" / "assinatura_densa.json"
    conexao = abrir_catalogo(tmp_path / "catalogo.sqlite")
    try:
        indexar([n], bruto=bruto, conexao=conexao, caminho_assinatura=caminho)
    finally:
        conexao.close()

    assert not caminho.exists()


def test_abrir_recuperador_recusa_indice_de_outro_encoder(tmp_path: Path):
    from copiloto.recuperacao.retriever import Recuperador

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "parametros.toml").write_text(
        Path("config/parametros.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    caminho = tmp_path / "indices" / NOME_DO_ARQUIVO
    gravar_assinatura(caminho, modelo=MODELO)
    caminho.write_text(
        caminho.read_text(encoding="utf-8").replace(assinatura_atual(MODELO)["fastembed"], "0.5.1"),
        encoding="utf-8",
    )

    # Nem bm25.json nem catalogo.sqlite existem em `tmp_path`: se a checagem
    # acontecesse depois de montar o pipeline, o erro seria outro. Exigir este
    # exato erro prova que ela vem antes de qualquer carga cara.
    with pytest.raises(IndiceDesatualizado):
        Recuperador.abrir(tmp_path)
