"""Gera `inventario.json` e `painel.html` (§16.5 do briefing).

**Sem React, sem build, sem CDN, sem framework de CSS e sem dependência
nova** — a mesma regra do §2.1 que já descarta CrewAI e LlamaIndex por
sobreposição vale aqui contra Jinja2 ou qualquer motor de template: o projeto
já tem `string.Template` na stdlib e o documento não precisa de lógica de
apresentação, só de dado formatado. O HTML é montado uma vez, aqui, a partir
de dado real — nenhum número é digitado à mão no template.

**Por que "abre com a rede desligada" não é sobre gerar offline.** É sobre
*ver* offline: o arquivo é gerado com a máquina que roda `evals/rodar.py` e
tem acesso ao repositório, mas o HTML resultante não carrega nada de fora —
sem CDN, sem `fetch`, sem script externo — então abri-lo depois, sem
internet, mostra exatamente o que foi gerado.
"""

from __future__ import annotations

import html
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from copiloto.governanca.coerencia import verificar
from copiloto.governanca.ficha import carregar_ficha
from copiloto.governanca.inventario import inventario
from copiloto.governanca.metricas import carregar_resultados, metricas_do_painel, roi_estimado

logger = logging.getLogger(__name__)

RAIZ_PADRAO = Path(__file__).resolve().parents[3]
# `governanca-out/` está no `.gitignore` desde a Fase 0 (ao lado de `/data` e
# `/indices`): o painel é regerado a partir de dado vivo a cada chamada, então
# comitá-lo o deixaria obsoleto no commit seguinte — a mesma razão por que
# `/data` não entra no versionamento.
JSON_PADRAO = RAIZ_PADRAO / "governanca-out" / "inventario.json"
HTML_PADRAO = RAIZ_PADRAO / "governanca-out" / "painel.html"


def historico_de_versoes(raiz: Path = RAIZ_PADRAO) -> list[dict[str, str]]:
    """A linha do tempo real de `config/governanca.yaml`, lida do `git log`.

    Não é um campo novo para preencher: é o histórico que o próprio
    versionamento do arquivo já tem. Um commit só (o que cria a ficha) é o
    estado inicial honesto — a lista cresce sozinha a cada mudança futura,
    sem exigir que ninguém mantenha um changelog paralelo.
    """
    caminho = raiz / "config" / "governanca.yaml"
    try:
        saida = subprocess.run(
            ["git", "log", "--follow", "--date=short", "--format=%h|%ad|%s", "--", str(caminho)],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as erro:
        logger.warning("histórico de versões indisponível", extra={"erro": str(erro)})
        return []
    if not saida:
        return [{"commit": "nao_commitado", "data": "", "mensagem": "ficha ainda não commitada"}]
    linhas = []
    for linha in saida.splitlines():
        commit, data, mensagem = linha.split("|", 2)
        linhas.append({"commit": commit, "data": data, "mensagem": mensagem})
    return linhas


def montar_payload(raiz: Path = RAIZ_PADRAO) -> dict[str, Any]:
    """Uma leitura só, reusada no JSON e no HTML — sem risco dos dois divergirem."""
    ficha = carregar_ficha()
    resultados = carregar_resultados()
    divergencias = verificar(ficha, raiz=raiz)
    return {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "identificacao": ficha.identificacao.model_dump(mode="json"),
        "classificacao_risco": ficha.classificacao_risco.model_dump(mode="json"),
        "inventario": inventario(raiz, modelos_da_ficha=tuple(ficha.modelos)),
        "coerencia": {
            "estado": "coerente" if not divergencias else "divergente",
            "divergencias": [d.como_dicionario() for d in divergencias],
        },
        "metricas": metricas_do_painel(resultados),
        "roi": roi_estimado(resultados),
        "historico_da_ficha": historico_de_versoes(raiz),
    }


# --- HTML ------------------------------------------------------------------

_ESTILO = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --linha:#ddd; --mudo:#666;
  --ok:#0a7d33; --ruim:#b3261e; --card:#f6f6f6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#15171a; --fg:#e8e8e8; --linha:#333; --mudo:#999; --ok:#5ec27a;
    --ruim:#ff6b60; --card:#1e2126; }
}
body { background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif;
  max-width:960px; margin:0 auto; padding:24px 16px 64px; line-height:1.5; }
h1 { font-size:1.4rem; }
h2 { font-size:1.1rem; margin-top:2rem; border-bottom:1px solid var(--linha);
  padding-bottom:.3rem; }
table { width:100%; border-collapse:collapse; margin:.5rem 0; font-size:.92rem; }
th, td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--linha); }
th { color:var(--mudo); font-weight:600; }
.selo { display:inline-block; padding:.1rem .5rem; border-radius:1rem; font-size:.8rem; }
.selo-ok { background:var(--ok); color:#fff; }
.selo-ruim { background:var(--ruim); color:#fff; }
.selo-mudo { background:var(--card); color:var(--mudo); border:1px solid var(--linha); }
.rodape { color:var(--mudo); font-size:.8rem; margin-top:2rem; }
code { background:var(--card); padding:.1rem .3rem; border-radius:.2rem; }
"""

_TEMPLATE = Template(
    """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<title>Governança — $nome</title>
<style>$estilo</style>
</head><body>
<h1>$nome — painel de governança</h1>
<p>Versão da ficha <code>$versao</code> · risco <code>$risco</code> · gerado em $gerado_em</p>

<h2>Coerência</h2>
<p><span class="selo $selo_coerencia_classe">$selo_coerencia_texto</span></p>
$tabela_divergencias

<h2>Frota de sistemas de IA</h2>
$tabela_frota

<h2>Métricas (última execução de evals/rodar.py)</h2>
<p class="rodape">medido em $medido_em · commit <code>$commit</code> · camadas $camadas</p>
$tabela_metricas

<h2>ROI</h2>
$tabela_roi

<h2>Linha do tempo da ficha</h2>
$tabela_historico

<p class="rodape">Gerado por <code>governanca/exportador.py</code> a partir de
<code>config/governanca.yaml</code> e <code>evals/resultados.json</code>. Nenhum valor
digitado à mão.</p>
</body></html>
"""
)


def _selo(status: object) -> tuple[str, str]:
    if status in ("aprovado", "coerente", "implementado", True):
        return "selo-ok", "ok"
    if status in ("reprovado", "divergente", False):
        return "selo-ruim", "reprovado"
    return "selo-mudo", "não medido"


def _tabela_divergencias(divergencias: list[dict[str, Any]]) -> str:
    if not divergencias:
        return "<p>Nenhuma divergência entre a ficha e o código.</p>"
    linhas = "".join(
        f"<tr><td>{html.escape(d['verificacao'])}</td><td>{html.escape(str(d['campo']))}</td>"
        f"<td>{html.escape(str(d['declarado']))}</td><td>{html.escape(str(d['real']))}</td></tr>"
        for d in divergencias
    )
    return (
        "<table><tr><th>verificação</th><th>campo</th><th>declarado</th><th>real</th></tr>"
        f"{linhas}</table>"
    )


def _tabela_frota(inv: dict[str, Any]) -> str:
    linhas = "".join(
        f"<tr><td><code>{html.escape(s['id_sistema'])}</code></td><td>{html.escape(s['papel'])}</td>"
        f"<td>{html.escape(s['origem'])}</td>"
        f'<td><span class="selo {"selo-ok" if s["implementado"] else "selo-mudo"}">'
        f"{'implementado' if s['implementado'] else 'previsto'}</span></td></tr>"
        for s in inv["sistemas"]
    )
    return (
        "<table><tr><th>id_sistema</th><th>papel</th><th>origem</th><th>estado</th></tr>"
        f"{linhas}</table>"
    )


def _tabela_metricas(bloco: dict[str, Any]) -> str:
    linhas = []
    for nome, valor in bloco["metricas"].items():
        if isinstance(valor, dict) and valor.get("status") == "nao_implementado":
            classe, texto, extra = "selo-mudo", "não implementado", html.escape(valor["motivo"])
        elif valor == "nao_medido":
            classe, texto, extra = "selo-mudo", "não medido", ""
        else:
            classe, texto = _selo(valor.get("aprovado"))
            extra = f"{valor.get('valor')} (exigido {valor.get('minimo', valor.get('maximo'))})"
        linhas.append(
            f"<tr><td><code>{html.escape(nome)}</code></td>"
            f'<td><span class="selo {classe}">{texto}</span></td><td>{extra}</td></tr>'
        )
    cabecalho = "<table><tr><th>métrica</th><th>estado</th><th>detalhe</th></tr>"
    return cabecalho + "".join(linhas) + "</table>"


def _tabela_roi(roi: dict[str, Any]) -> str:
    return (
        "<table>"
        f'<tr><th>tipo</th><td><span class="selo selo-mudo">premissa declarada</span></td></tr>'
        "<tr><th>minutos de busca manual (estimado)</th>"
        f"<td>{roi['minutos_busca_manual_estimados']}</td></tr>"
        f'<tr><th>custo real por consulta</th><td><span class="selo selo-mudo">'
        f"não implementado</span> — {html.escape(roi['custo_por_consulta']['motivo'])}</td></tr>"
        "</table>"
        f'<p class="rodape">{html.escape(roi["nota"])}</p>'
    )


def _tabela_historico(historico: list[dict[str, str]]) -> str:
    if not historico:
        return "<p>Sem histórico disponível (fora de um repositório git).</p>"
    linhas = "".join(
        f"<tr><td><code>{html.escape(h['commit'])}</code></td><td>{html.escape(h['data'])}</td>"
        f"<td>{html.escape(h['mensagem'])}</td></tr>"
        for h in historico
    )
    return "<table><tr><th>commit</th><th>data</th><th>mensagem</th></tr>" + linhas + "</table>"


def renderizar_html(payload: dict[str, Any]) -> str:
    classe_coerencia, texto_coerencia = _selo(payload["coerencia"]["estado"])
    return _TEMPLATE.substitute(
        nome=html.escape(payload["identificacao"]["nome"]),
        estilo=_ESTILO,
        versao=html.escape(payload["identificacao"]["versao"]),
        risco=html.escape(payload["classificacao_risco"]["nivel"]),
        gerado_em=html.escape(payload["gerado_em"]),
        selo_coerencia_classe=classe_coerencia,
        selo_coerencia_texto=texto_coerencia,
        tabela_divergencias=_tabela_divergencias(payload["coerencia"]["divergencias"]),
        tabela_frota=_tabela_frota(payload["inventario"]),
        medido_em=html.escape(payload["metricas"]["medido_em"] or "nunca"),
        commit=html.escape(payload["metricas"]["commit"] or "?"),
        camadas=html.escape(str(payload["metricas"]["camadas_medidas"])),
        tabela_metricas=_tabela_metricas(payload["metricas"]),
        tabela_roi=_tabela_roi(payload["roi"]),
        tabela_historico=_tabela_historico(payload["historico_da_ficha"]),
    )


def gerar(
    raiz: Path = RAIZ_PADRAO, *, destino_json: Path = JSON_PADRAO, destino_html: Path = HTML_PADRAO
) -> dict[str, Any]:
    """Escreve os dois arquivos a partir de uma leitura só. Devolve o payload."""
    payload = montar_payload(raiz)
    destino_json.parent.mkdir(parents=True, exist_ok=True)
    conteudo = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    destino_json.write_text(conteudo, encoding="utf-8")
    destino_html.write_text(renderizar_html(payload), encoding="utf-8")
    return payload


if __name__ == "__main__":
    resultado = gerar()
    # `print` porque isto é script de linha de comando (§11 proíbe em código
    # de produção, não aqui).
    print(f"painel em {HTML_PADRAO}")
    print(f"inventario em {JSON_PADRAO}")
    print(f"coerência: {resultado['coerencia']['estado']}")
