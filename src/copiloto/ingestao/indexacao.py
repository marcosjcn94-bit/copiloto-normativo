"""Bruto coletado -> catálogo SQL, índice denso (Chroma) e índice esparso (BM25).

Divisão de trabalho que o projeto inteiro assume: **metadado é SQL, trecho é
índice**. "Quais normas sobre nuvem estão vigentes?" é `SELECT`, não busca
vetorial — a resposta precisa ser completa e exata, e similaridade não garante
nenhuma das duas coisas (§7 do briefing).

Reexecutar não rebaixa nada: o catálogo faz `upsert` por `id_norma` e os chunks
têm id determinístico, então reindexar substitui em vez de duplicar.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from copiloto.ingestao.chunking import ChunkFilho, ChunkPai, Norma, montar_chunks
from copiloto.ingestao.coleta import NormaDoCorpus, caminho_do_bruto
from copiloto.ingestao.extracao import acentuacao_intacta, extrair_blocos

logger = logging.getLogger(__name__)

# Nome da coleção e modelo migram para `config/parametros.toml` na Fase 3, junto
# com os demais parâmetros de recuperação.
COLECAO_DENSA = "normativos_bcb"
MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS normas (
    id_norma        TEXT PRIMARY KEY,
    tipo            TEXT NOT NULL,
    numero          TEXT NOT NULL,
    ano             INTEGER NOT NULL,
    data            TEXT NOT NULL,
    tema            TEXT NOT NULL,
    status_vigencia TEXT NOT NULL CHECK (status_vigencia IN ('vigente', 'revogada')),
    url             TEXT NOT NULL,
    titulo          TEXT NOT NULL DEFAULT '',
    ementa          TEXT NOT NULL DEFAULT '',
    artigos         INTEGER NOT NULL DEFAULT 0,
    atualizado_em   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_normas_tema ON normas (tema);
CREATE INDEX IF NOT EXISTS idx_normas_status ON normas (status_vigencia);

-- O chunk pai (artigo inteiro) mora aqui: na Fase 3 o retriever acha o filho no
-- indice e busca o artigo por id, que e o "vetorizar o paragrafo, entregar o
-- artigo" do briefing sem duplicar o texto dentro do indice vetorial.
CREATE TABLE IF NOT EXISTS artigos (
    id            TEXT PRIMARY KEY,
    id_norma      TEXT NOT NULL REFERENCES normas (id_norma),
    numero_artigo INTEGER NOT NULL,
    artigo        TEXT NOT NULL,
    capitulo      TEXT NOT NULL DEFAULT '',
    secao         TEXT NOT NULL DEFAULT '',
    texto         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artigos_norma ON artigos (id_norma, numero_artigo);
"""


class Embedder(Protocol):
    """O mínimo que a indexação precisa de um modelo de embeddings."""

    def embed(self, textos: Iterable[str]) -> Iterable[Any]: ...


@dataclass
class RelatorioDeIndexacao:
    normas: int = 0
    artigos: int = 0
    chunks: int = 0
    sem_texto: list[str] = field(default_factory=list)
    acentuacao_quebrada: list[str] = field(default_factory=list)


def abrir_catalogo(caminho: Path) -> sqlite3.Connection:
    """Abre (e cria, se preciso) o catálogo SQL."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row
    conexao.executescript(ESQUEMA)
    return conexao


def registrar_norma(
    conexao: sqlite3.Connection,
    norma: NormaDoCorpus,
    *,
    status_vigencia: str,
    titulo: str = "",
    ementa: str = "",
    artigos: int = 0,
) -> None:
    """`upsert` por `id_norma` — reexecutar atualiza, nunca duplica."""
    conexao.execute(
        """
        INSERT INTO normas (id_norma, tipo, numero, ano, data, tema, status_vigencia,
                            url, titulo, ementa, artigos, atualizado_em)
        VALUES (:id_norma, :tipo, :numero, :ano, :data, :tema, :status_vigencia,
                :url, :titulo, :ementa, :artigos, :atualizado_em)
        ON CONFLICT (id_norma) DO UPDATE SET
            tipo = excluded.tipo,
            numero = excluded.numero,
            ano = excluded.ano,
            data = excluded.data,
            tema = excluded.tema,
            status_vigencia = excluded.status_vigencia,
            url = excluded.url,
            titulo = excluded.titulo,
            ementa = excluded.ementa,
            -- uma reexecução que não conseguiu extrair artigo nenhum não pode
            -- zerar a contagem que a execução anterior obteve
            artigos = MAX(excluded.artigos, normas.artigos),
            atualizado_em = excluded.atualizado_em
        """,
        {
            "id_norma": norma.id_norma,
            "tipo": norma.tipo,
            "numero": norma.numero,
            "ano": norma.ano,
            "data": norma.data.isoformat(),
            "tema": norma.tema,
            "status_vigencia": status_vigencia,
            "url": norma.url,
            "titulo": titulo,
            "ementa": ementa,
            "artigos": artigos,
            "atualizado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )


def tokenizar(texto: str) -> list[str]:
    """Tokenização do BM25: minúsculas, sem acento, com os números intactos.

    O número da norma é o termo que mais importa aqui — `4.658` precisa
    sobreviver à tokenização, senão o índice esparso perde a razão de existir.
    """
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = sem_acento.lower().replace(".", "").replace("º", "")
    return [token for token in "".join(c if c.isalnum() else " " for c in limpo).split() if token]


def indexar(
    normas: Sequence[NormaDoCorpus],
    *,
    bruto: Path,
    conexao: sqlite3.Connection,
    colecao: Any | None = None,
    embedder: Embedder | None = None,
    caminho_bm25: Path | None = None,
) -> RelatorioDeIndexacao:
    """Percorre o bruto coletado e alimenta catálogo, denso e esparso.

    `colecao` e `embedder` são opcionais para que o catálogo e o BM25 possam ser
    testados sem carregar modelo ONNX — um único modelo por vez é regra de
    máquina (§3.1 do briefing).
    """
    relatorio = RelatorioDeIndexacao()
    todos_filhos: list[ChunkFilho] = []

    for norma in normas:
        arquivo = caminho_do_bruto(bruto, norma)
        if not arquivo.exists():
            relatorio.sem_texto.append(norma.id_norma)
            continue
        registro = json.loads(arquivo.read_text(encoding="utf-8"))
        texto = registro.get("Texto") or ""
        if not acentuacao_intacta(texto):
            relatorio.acentuacao_quebrada.append(norma.id_norma)

        # A vigencia vale a da fonte, nao a declarada no corpus.toml: o TOML e
        # curadoria de um dia, o campo `Revogado` e o estado de agora.
        status = "revogada" if registro.get("Revogado") else "vigente"
        identificacao = Norma(
            tipo=norma.tipo, numero=norma.numero, ano=norma.ano, revogada=status == "revogada"
        )
        pais, filhos = montar_chunks(extrair_blocos(texto), identificacao)
        registrar_norma(
            conexao,
            norma,
            status_vigencia=status,
            titulo=str(registro.get("Titulo") or ""),
            ementa=str(registro.get("Assunto") or "").strip(),
            artigos=len(pais),
        )
        if not pais:
            relatorio.sem_texto.append(norma.id_norma)
        _registrar_artigos(conexao, pais)
        todos_filhos.extend(filhos)
        relatorio.normas += 1
        relatorio.artigos += len(pais)

    conexao.commit()
    relatorio.chunks = len(todos_filhos)

    if caminho_bm25 is not None:
        _gravar_bm25(todos_filhos, caminho_bm25)
    if colecao is not None and embedder is not None:
        _indexar_denso(todos_filhos, colecao=colecao, embedder=embedder)

    logger.info(
        "indexacao concluida",
        extra={
            "normas": relatorio.normas,
            "artigos": relatorio.artigos,
            "chunks": relatorio.chunks,
            "sem_texto": len(relatorio.sem_texto),
        },
    )
    return relatorio


def _registrar_artigos(conexao: sqlite3.Connection, pais: Sequence[ChunkPai]) -> None:
    """Grava o artigo inteiro. `INSERT OR REPLACE` porque o id e deterministico."""
    conexao.executemany(
        """
        INSERT OR REPLACE INTO artigos (id, id_norma, numero_artigo, artigo, capitulo, secao, texto)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (p.id, p.id_norma, p.numero_artigo, p.artigo, p.capitulo or "", p.secao or "", p.texto)
            for p in pais
        ],
    )


def _gravar_bm25(filhos: Sequence[ChunkFilho], caminho: Path) -> None:
    """Grava o índice esparso.

    `rank_bm25` não persiste sozinho: o que vai para o disco é o corpus
    tokenizado e os ids, e o índice é reconstruído no carregamento. Guardar o
    objeto pronto amarraria o arquivo à versão da biblioteca.

    JSON e não `pickle`: o conteúdo é lista de string e desserializar `pickle`
    executa código. Um índice é arquivo — arquivo não deve poder rodar nada.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ids": [filho.id for filho in filhos],
        "tokens": [tokenizar(filho.texto_indexavel) for filho in filhos],
    }
    caminho.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _indexar_denso(filhos: Sequence[ChunkFilho], *, colecao: Any, embedder: Embedder) -> None:
    """`upsert` no Chroma: id determinístico, então reindexar substitui."""
    if not filhos:
        return
    vetores = [vetor.tolist() for vetor in embedder.embed(f.texto_indexavel for f in filhos)]
    colecao.upsert(
        ids=[f.id for f in filhos],
        embeddings=vetores,
        documents=[f.texto_indexavel for f in filhos],
        metadatas=[metadata_do_chunk(f) for f in filhos],
    )


def metadata_do_chunk(filho: ChunkFilho) -> dict[str, Any]:
    """Metadata que acompanha o trecho até a citação final."""
    return {
        "id_pai": filho.id_pai,
        "id_norma": filho.id_norma,
        "norma": filho.norma,
        "artigo": filho.artigo,
        "numero_artigo": filho.numero_artigo,
        "rotulo": filho.rotulo,
        "capitulo": filho.capitulo or "",
        "secao": filho.secao or "",
        "revogada": filho.revogada,
    }


def executar_pipeline(
    raiz: Path, *, forcar: bool = False, com_denso: bool = True
) -> RelatorioDeIndexacao:
    """Coleta o corpus declarado e reconstrói catálogo, BM25 e índice denso.

    Ponto de entrada do `python -m copiloto.ingestao.indexacao`. É idempotente
    ponta a ponta: rodar duas vezes seguidas não baixa de novo e não duplica
    linha nenhuma.
    """
    from copiloto.ingestao.coleta import buscar_no_bcb, carregar_corpus, coletar

    corpus = carregar_corpus(raiz / "config" / "corpus.toml")
    bruto = raiz / "data" / "bruto"
    coleta = coletar(corpus, bruto, buscar=buscar_no_bcb(), forcar=forcar)
    if coleta.falhas:
        logger.error("coleta incompleta", extra={"falhas": coleta.falhas})
    if coleta.divergencias_de_vigencia:
        logger.warning(
            "vigencia declarada difere da coletada",
            extra={"divergencias": coleta.divergencias_de_vigencia},
        )

    colecao = embedder = None
    if com_denso:
        import chromadb
        from fastembed import TextEmbedding

        cliente = chromadb.PersistentClient(path=str(raiz / "indices" / "chroma"))
        colecao = cliente.get_or_create_collection(
            name=COLECAO_DENSA, metadata={"hnsw:space": "cosine"}
        )
        embedder = TextEmbedding(model_name=MODELO_EMBEDDING)

    conexao = abrir_catalogo(raiz / "data" / "catalogo.sqlite")
    try:
        return indexar(
            corpus.normas,
            bruto=bruto,
            conexao=conexao,
            colecao=colecao,
            embedder=embedder,
            caminho_bm25=raiz / "indices" / "bm25.json",
        )
    finally:
        conexao.close()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    executar_pipeline(Path(__file__).resolve().parents[3])
