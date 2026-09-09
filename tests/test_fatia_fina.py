"""Critério de pronto da Fase 1 — a fatia fina ponta a ponta.

Três normativos reais do BCB: baixar, extrair, quebrar por artigo, indexar no
Chroma e fazer **uma** consulta. O que este teste prova é o que o briefing manda
provar antes de qualquer outra coisa:

1. a acentuação chega intacta ao texto extraído;
2. o trecho recuperado carrega o número do artigo correto na metadata;
3. vigência é campo da metadata, não frase no texto.

Se qualquer um dos três falhar, o resto do projeto não se sustenta.

Provisório por desenho: o download vira `ingestao/coleta.py` na Fase 2 e o
acesso ao Chroma vira `recuperacao/adapters/chroma.py` na Fase 3 — nenhum
módulo de `src/` importa `chromadb` direto.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from copiloto.ingestao.chunking import ChunkFilho, Norma, montar_chunks
from copiloto.ingestao.extracao import acentuacao_intacta, extrair_blocos

pytestmark = pytest.mark.rede

# Endpoint da Busca de Normas do BCB, confirmado por requisição (§4.1 do briefing).
# `p1` é o tipo do normativo e `p2` o número. A URL definitiva vai para
# `config/corpus.toml` na Fase 2.
ENDPOINT = "https://www.bcb.gov.br/api/conteudo/app/normativos/exibenormativo"

# Tema coeso: segurança cibernética e contratação de nuvem. A 4.658 entra
# revogada de propósito — a fatia fina precisa exercitar o campo de vigência.
NORMATIVOS = [("Resolução CMN", "4893"), ("Resolução BCB", "85"), ("Resolução", "4658")]

MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CACHE = Path(__file__).resolve().parents[1] / "data" / "bruto"
RE_ARTIGO = re.compile(r"^Art\. \d+º$")


@dataclass(frozen=True)
class Fatia:
    filhos: list[ChunkFilho]
    normas: dict[str, Norma]


def _baixar(tipo: str, numero: str) -> dict:
    import httpx

    destino = CACHE / f"{re.sub(r'[^a-z0-9]+', '-', tipo.lower())}-{numero}.json"
    if destino.exists():
        return json.loads(destino.read_text(encoding="utf-8"))
    try:
        resposta = httpx.get(ENDPOINT, params={"p1": tipo, "p2": numero}, timeout=60)
        resposta.raise_for_status()
        conteudo = resposta.json().get("conteudo") or []
    except Exception as erro:  # noqa: BLE001 - sem rede o critério não é avaliável
        pytest.skip(f"BCB indisponível para {tipo} {numero}: {erro}")
    if not conteudo:
        pytest.skip(f"BCB não devolveu conteúdo para {tipo} {numero}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(conteudo[0], ensure_ascii=False), encoding="utf-8")
    return conteudo[0]


@pytest.fixture(scope="module")
def fatia() -> Fatia:
    filhos: list[ChunkFilho] = []
    normas: dict[str, Norma] = {}
    for tipo, numero in NORMATIVOS:
        registro = _baixar(tipo, numero)
        norma = Norma(
            tipo=registro["Tipo"],
            numero=str(int(float(registro["Numero"]))),
            ano=int(registro["Data"][:4]),
            revogada=bool(registro["Revogado"]),
        )
        _, filhos_da_norma = montar_chunks(extrair_blocos(registro["Texto"]), norma)
        assert filhos_da_norma, f"{norma.id_norma} não produziu chunk"
        filhos.extend(filhos_da_norma)
        normas[norma.id_norma] = norma
    return Fatia(filhos=filhos, normas=normas)


@pytest.fixture(scope="module")
def colecao(fatia: Fatia):
    import chromadb
    from fastembed import TextEmbedding

    modelo = TextEmbedding(model_name=MODELO_EMBEDDING)
    vetores = [v.tolist() for v in modelo.embed(f.texto_indexavel for f in fatia.filhos)]
    colecao = chromadb.EphemeralClient().create_collection(
        name="fatia_fina", metadata={"hnsw:space": "cosine"}
    )
    colecao.add(
        ids=[f.id for f in fatia.filhos],
        embeddings=vetores,
        documents=[f.texto_indexavel for f in fatia.filhos],
        metadatas=[
            {
                "id_norma": f.id_norma,
                "norma": f.norma,
                "artigo": f.artigo,
                "numero_artigo": f.numero_artigo,
                "rotulo": f.rotulo,
                "capitulo": f.capitulo or "",
                "revogada": f.revogada,
            }
            for f in fatia.filhos
        ],
    )
    return colecao, modelo


def _consultar(colecao, pergunta: str, k: int = 5) -> list[dict]:
    chroma, modelo = colecao
    vetor = next(iter(modelo.embed([pergunta]))).tolist()
    return chroma.query(query_embeddings=[vetor], n_results=k)["metadatas"][0]


def test_acentuacao_intacta_em_todo_o_corpus_da_fatia(fatia: Fatia) -> None:
    quebrados = [f.rotulo for f in fatia.filhos if not acentuacao_intacta(f.texto)]
    assert not quebrados, f"acentuação quebrada em {len(quebrados)} chunks"
    assert any("ç" in f.texto or "ã" in f.texto for f in fatia.filhos)


def test_os_tres_normativos_entraram_no_indice(fatia: Fatia) -> None:
    assert len(fatia.normas) == 3
    assert len(fatia.filhos) > 300


def test_consulta_conceitual_devolve_artigo_na_metadata(colecao) -> None:
    resultados = _consultar(colecao, "requisitos para contratação de computação em nuvem")
    assert len(resultados) == 5
    for metadata in resultados:
        assert RE_ARTIGO.match(metadata["artigo"]), metadata["artigo"]
        assert metadata["rotulo"].startswith(metadata["artigo"])
        assert metadata["numero_artigo"] >= 1


def test_consulta_conceitual_cai_no_capitulo_certo(colecao) -> None:
    """O gabarito é o capítulo, não um artigo escolhido a dedo.

    A Resolução CMN 4.893 trata contratação de nuvem em um capítulo inteiro;
    exigir um artigo específico aqui seria fixar o teste no resultado que o
    modelo por acaso deu. `recall@k` por artigo é medida da Fase 3, com gabarito
    escrito antes de rodar a busca.
    """
    resultados = _consultar(colecao, "requisitos para contratação de computação em nuvem")
    nuvem = [
        m
        for m in resultados
        if m["id_norma"] == "resolucao-cmn-4893-2021" and "NUVEM" in (m["capitulo"] or "")
    ]
    assert nuvem, [(m["id_norma"], m["rotulo"], m["capitulo"]) for m in resultados]


def test_vigencia_e_campo_da_metadata(fatia: Fatia) -> None:
    revogadas = {n.id_norma for n in fatia.normas.values() if n.revogada}
    assert "resolucao-4658-2018" in revogadas
    assert not fatia.normas["resolucao-cmn-4893-2021"].revogada
