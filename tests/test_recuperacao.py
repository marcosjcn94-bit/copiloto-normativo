"""Orquestração da recuperação, sem carregar um único modelo ONNX.

O que se prova aqui é a costura: que o id achado só pelo BM25 chega hidratado ao
fim, que o que se entrega é o artigo e não o parágrafo, e que a vigência
sobrevive até a citação. A qualidade da recuperação não se prova com `assert` —
ela está medida em `evals/ablacao.md`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from copiloto.recuperacao.adapters.base import AdaptadorVetorial, Ocorrencia
from copiloto.recuperacao.retriever import ParametrosRecuperacao, Recuperador

PARAMETROS = ParametrosRecuperacao(
    k_denso=10,
    k_esparso=10,
    k_rrf=60,
    k_final=2,
    score_minimo=0.0,
    colecao="teste",
    modelo_embedding="irrelevante",
    modelo_rerank="irrelevante",
)


def _ocorrencia(id_: str, *, pai: str, artigo: int, revogada: bool = False) -> Ocorrencia:
    return Ocorrencia(
        id=id_,
        texto=f"texto do chunk {id_}",
        metadata={
            "id_pai": pai,
            "id_norma": "resolucao-bcb-85-2021",
            "norma": "Resolução BCB nº 85, de 2021",
            "artigo": f"Art. {artigo}º",
            "numero_artigo": artigo,
            "revogada": revogada,
        },
        score=0.0,
    )


class AdaptadorFalso:
    """`AdaptadorVetorial` em memória. `buscar` devolve só o que foi combinado."""

    def __init__(self, tudo: Sequence[Ocorrencia], *, devolve: Sequence[str]) -> None:
        self._tudo = {o.id: o for o in tudo}
        self._devolve = list(devolve)
        self.ids_hidratados: list[str] = []

    def buscar(
        self, vetor: Sequence[float], *, k: int, filtro: Mapping[str, Any] | None = None
    ) -> list[Ocorrencia]:
        return [self._tudo[id_] for id_ in self._devolve[:k]]

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        self.ids_hidratados.extend(ids)
        return {id_: self._tudo[id_] for id_ in ids if id_ in self._tudo}


class DensaFalsa:
    """Substitui `BuscaDensa` sem carregar `fastembed`."""

    def __init__(self, adaptador: AdaptadorVetorial) -> None:
        self._adaptador = adaptador

    def buscar(
        self, pergunta: str, *, k: int, filtro: Mapping[str, Any] | None = None
    ) -> list[Ocorrencia]:
        return self._adaptador.buscar([0.0], k=k, filtro=filtro)

    def obter(self, ids: Sequence[str]) -> dict[str, Ocorrencia]:
        return self._adaptador.obter(ids)


class EsparsaFalsa:
    def __init__(self, resultado: Sequence[tuple[str, float]]) -> None:
        self._resultado = list(resultado)

    def buscar(self, pergunta: str, *, k: int) -> list[tuple[str, float]]:
        return self._resultado[:k]


class ReordenadorFalso:
    """Inverte a ordem — mudança visível sem cross-encoder nenhum."""

    def reordenar(self, pergunta: str, ocorrencias: Sequence[Ocorrencia]) -> list[Ocorrencia]:
        return list(reversed(ocorrencias))


def montar(
    *,
    denso: Sequence[str],
    esparso: Sequence[tuple[str, float]],
    tudo: Sequence[Ocorrencia],
    catalogo: sqlite3.Connection | None = None,
) -> tuple[Recuperador, AdaptadorFalso]:
    adaptador = AdaptadorFalso(tudo, devolve=denso)
    recuperador = Recuperador(
        densa=DensaFalsa(adaptador),  # type: ignore[arg-type]
        esparsa=EsparsaFalsa(esparso),  # type: ignore[arg-type]
        reordenador=ReordenadorFalso(),  # type: ignore[arg-type]
        parametros=PARAMETROS,
        catalogo=catalogo,
    )
    return recuperador, adaptador


def test_id_que_so_o_bm25_achou_chega_hidratado_ao_fim() -> None:
    """O caso que justifica o índice esparso existir.

    `c2` não aparece na busca vetorial. Se ele não chegar ao resultado com
    texto, o BM25 vira enfeite: acha o artigo e não consegue entregá-lo.
    """
    tudo = [_ocorrencia("c1", pai="p1", artigo=1), _ocorrencia("c2", pai="p2", artigo=2)]
    recuperador, adaptador = montar(denso=["c1"], esparso=[("c2", 8.0)], tudo=tudo)

    trechos = recuperador.buscar("qualquer", modo="hibrido")

    assert {t.numero_artigo for t in trechos} == {1, 2}
    assert adaptador.ids_hidratados == ["c2"], "hidratou o que já tinha vindo do denso"
    assert all(t.texto for t in trechos)


def test_modo_denso_ignora_o_esparso() -> None:
    """Sem isso, a linha 'só vetorial' da ablação mediria o híbrido."""
    tudo = [_ocorrencia("c1", pai="p1", artigo=1), _ocorrencia("c2", pai="p2", artigo=2)]
    recuperador, _ = montar(denso=["c1"], esparso=[("c2", 99.0)], tudo=tudo)

    trechos = recuperador.buscar("qualquer", modo="denso")

    assert [t.numero_artigo for t in trechos] == [1]


def test_filhos_do_mesmo_artigo_viram_um_trecho_so() -> None:
    """`k_final` significa cinco artigos, não cinco parágrafos do mesmo artigo."""
    tudo = [
        _ocorrencia("c1", pai="p1", artigo=1),
        _ocorrencia("c2", pai="p1", artigo=1),
        _ocorrencia("c3", pai="p2", artigo=2),
    ]
    recuperador, _ = montar(denso=["c1", "c2", "c3"], esparso=[], tudo=tudo)

    trechos = recuperador.buscar("qualquer", modo="denso")

    assert [t.id for t in trechos] == ["p1", "p2"]


def test_k_final_corta_a_entrega() -> None:
    tudo = [_ocorrencia(f"c{i}", pai=f"p{i}", artigo=i) for i in range(1, 5)]
    recuperador, _ = montar(denso=[o.id for o in tudo], esparso=[], tudo=tudo)

    assert len(recuperador.buscar("qualquer", modo="denso")) == PARAMETROS.k_final
    assert len(recuperador.buscar("qualquer", modo="denso", k_final=1)) == 1


def test_rerank_reordena_a_entrega() -> None:
    tudo = [_ocorrencia("c1", pai="p1", artigo=1), _ocorrencia("c2", pai="p2", artigo=2)]
    recuperador, _ = montar(denso=["c1", "c2"], esparso=[], tudo=tudo)

    sem_rerank = recuperador.buscar("qualquer", modo="hibrido")
    com_rerank = recuperador.buscar("qualquer", modo="hibrido_rerank")

    assert [t.id for t in com_rerank] == list(reversed([t.id for t in sem_rerank]))


def test_score_minimo_so_atua_no_modo_com_rerank() -> None:
    """RRF devolve algo como 0,016; um corte em 0,5 zeraria o híbrido puro.

    O threshold compara score de cross-encoder. Aplicá-lo antes do rerank
    esvaziaria o resultado sempre — e silenciosamente.
    """
    import dataclasses

    tudo = [_ocorrencia("c1", pai="p1", artigo=1)]
    recuperador, _ = montar(denso=["c1"], esparso=[("c1", 1.0)], tudo=tudo)
    recuperador._parametros = dataclasses.replace(PARAMETROS, score_minimo=0.5)

    assert recuperador.buscar("qualquer", modo="hibrido"), "o corte vazou para fora do rerank"


def test_modo_desconhecido_falha_alto() -> None:
    recuperador, _ = montar(denso=[], esparso=[], tudo=[])
    with pytest.raises(ValueError, match="modo desconhecido"):
        recuperador.buscar("qualquer", modo="hibrido_sem_rerank")  # type: ignore[arg-type]


def test_entrega_o_artigo_do_catalogo_e_nao_o_chunk(tmp_path) -> None:
    """'Vetorizar o parágrafo, entregar o artigo' — verificado, não afirmado."""
    conexao = sqlite3.connect(tmp_path / "catalogo.sqlite")
    conexao.row_factory = sqlite3.Row
    conexao.execute(
        "CREATE TABLE artigos (id TEXT PRIMARY KEY, id_norma TEXT, numero_artigo INTEGER, "
        "artigo TEXT, capitulo TEXT, secao TEXT, texto TEXT)"
    )
    conexao.execute(
        "INSERT INTO artigos VALUES ('p1', 'resolucao-bcb-85-2021', 7, 'Art. 7º', '', '', ?)",
        ("Art. 7º As instituições devem designar diretor responsável ...",),
    )
    conexao.commit()

    tudo = [_ocorrencia("c1", pai="p1", artigo=7)]
    recuperador, _ = montar(denso=["c1"], esparso=[], tudo=tudo, catalogo=conexao)

    (trecho,) = recuperador.buscar("qualquer", modo="denso")

    assert trecho.texto.startswith("Art. 7º As instituições")
    assert "texto do chunk" not in trecho.texto


def test_citacao_avisa_quando_a_norma_esta_revogada() -> None:
    """Citar norma revogada sem o aviso é o pior erro que este projeto pode cometer."""
    tudo = [_ocorrencia("c1", pai="p1", artigo=2, revogada=True)]
    recuperador, _ = montar(denso=["c1"], esparso=[], tudo=tudo)

    (trecho,) = recuperador.buscar("qualquer", modo="denso")

    assert trecho.revogada
    assert "REVOGADA" in trecho.citacao


def test_chunk_sem_pai_e_descartado_e_nao_citado_sem_artigo() -> None:
    sem_pai = Ocorrencia(id="c1", texto="orfao", metadata={"id_norma": "x"}, score=0.0)
    recuperador, _ = montar(denso=["c1"], esparso=[], tudo=[sem_pai])

    assert recuperador.buscar("qualquer", modo="denso") == []
