"""A fusão é a única parte do pipeline que roda sem modelo — e é testada como tal."""

from __future__ import annotations

import pytest

from copiloto.recuperacao.fusao import rrf


def test_concordancia_entre_listas_vence_primeiro_lugar_isolado() -> None:
    """A tese do RRF: aparecer bem nas duas buscas vale mais que liderar uma.

    `b` é 2º no denso e 2º no esparso; `a` é 1º no denso e não aparece no
    esparso. Se este teste inverter, a fusão virou "confie no denso" e o índice
    esparso deixou de ter função.
    """
    fundido = rrf([["a", "b", "c"], ["d", "b", "e"]], k_rrf=60)
    assert fundido[0][0] == "b"


def test_ordem_e_deterministica_no_empate() -> None:
    """Sem desempate estável, a tabela de ablação mudaria entre execuções."""
    primeira = rrf([["z", "a"], ["a", "z"]], k_rrf=60)
    segunda = rrf([["a", "z"], ["z", "a"]], k_rrf=60)
    assert [id_ for id_, _ in primeira] == [id_ for id_, _ in segunda] == ["a", "z"]


def test_id_repetido_na_mesma_lista_conta_uma_vez() -> None:
    """Duplicata dentro de uma origem não pode valer como concordância."""
    (unico,) = rrf([["a", "a", "a"]], k_rrf=60)
    assert unico == ("a", pytest.approx(1 / 61))


def test_lista_vazia_nao_quebra() -> None:
    assert rrf([[], []], k_rrf=60) == []


def test_k_rrf_precisa_ser_positivo() -> None:
    """k_rrf = -1 daria divisão por zero no primeiro posto — falhar é melhor."""
    with pytest.raises(ValueError, match="k_rrf"):
        rrf([["a"]], k_rrf=0)
