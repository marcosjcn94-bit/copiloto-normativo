"""Fase 9 — `painel.html` e `inventario.json` (§16.5 do briefing).

O painel tem de abrir com a rede desligada: nenhuma referência a CDN,
`fetch` ou script externo pode entrar no HTML gerado.
"""

from __future__ import annotations

import json
from pathlib import Path

from copiloto.governanca.exportador import gerar


def test_gerar_escreve_json_e_html_coerentes(tmp_path: Path) -> None:
    destino_json = tmp_path / "inventario.json"
    destino_html = tmp_path / "painel.html"

    payload = gerar(destino_json=destino_json, destino_html=destino_html)

    lido = json.loads(destino_json.read_text(encoding="utf-8"))
    assert lido["coerencia"]["estado"] == payload["coerencia"]["estado"]
    assert destino_html.exists()


def test_painel_nao_referencia_nada_externo(tmp_path: Path) -> None:
    destino_html = tmp_path / "painel.html"
    gerar(destino_json=tmp_path / "inventario.json", destino_html=destino_html)

    html = destino_html.read_text(encoding="utf-8")
    for proibido in ("cdn.", "https://", "http://", "<script"):
        assert proibido not in html, f"{proibido!r} não pode aparecer: painel deve abrir offline"


def test_painel_mostra_a_frota_real(tmp_path: Path) -> None:
    destino_html = tmp_path / "painel.html"
    gerar(destino_json=tmp_path / "inventario.json", destino_html=destino_html)

    html = destino_html.read_text(encoding="utf-8")
    assert "groq" in html
    assert "eval-judge/camada-3" in html
