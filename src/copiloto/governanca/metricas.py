"""As métricas do painel de governança (§16.4/§16.5), lidas do que já mede.

**Fonte única de verdade: `evals/resultados.json`.** É o que a régua de
`.claude/rules/observabilidade.md` exige — `recall@5` na ablação e `recall@5`
no painel têm de ser o mesmo número, calculado do mesmo jeito. Este módulo
não recalcula nada: lê o payload que `evals/rodar.py` já escreveu, com
`medido_em` e `commit` de procedência, e devolve `nao_medido` para o que essa
execução não cobriu — nunca um número inventado.

**O que este módulo NÃO mede, e por quê.** `custo_por_consulta`,
`latencia_p95`, `taxa_citacao_orfa` e a razão aprovado/rejeitado no HITL
exigiriam consultar a API de leitura do Langfuse (`client.api.trace.list`
ou equivalente) — integração nova, não testável contra conta real nesta
sessão, e o SDK trocou essa superfície entre v3 e v4 recentemente (ver
`observabilidade/tracing.py`, que só *escreve* trace, nunca lê de volta).
Implementar isso sem poder verificar contra um projeto Langfuse de verdade
arrisca exatamente o que o §16.6 pede para evitar: um número que parece
medido e não é. Fica declarado como lacuna — ver `docs/GOVERNANCA_IA.md` e a
tabela "O que NÃO foi implementado" do README — e não como `nao_medido`
inventado por uma função que nunca teve chance de medir de verdade.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RAIZ_PADRAO = Path(__file__).resolve().parents[3]
RESULTADOS_PADRAO = RAIZ_PADRAO / "evals" / "resultados.json"

# As quatro métricas do §16.4 que dependem de consultar o Langfuse de volta —
# não construído nesta fase (ver o cabeçalho do módulo). Aparecem no painel
# com este motivo, e não como `nao_medido` genérico, porque a ausência tem
# uma causa específica e é isso que o §16.6 pede para não maquiar.
_NAO_IMPLEMENTADAS = {
    "custo_por_consulta": "requer consulta de leitura ao Langfuse — não implementado nesta fase",
    "latencia_p95": "requer consulta de leitura ao Langfuse — não implementado nesta fase",
    "taxa_citacao_orfa": "requer consulta de leitura ao Langfuse — não implementado nesta fase",
    "razao_aprovado_rejeitado_hitl": (
        "requer consulta de leitura ao Langfuse — não implementado nesta fase"
    ),
}


def carregar_resultados(caminho: Path = RESULTADOS_PADRAO) -> dict[str, Any] | None:
    """O payload do último `evals/rodar.py`, ou `None` se nunca rodou."""
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        logger.warning("resultados.json ausente ou ilegível", extra={"erro": str(erro)})
        return None


def metricas_do_painel(resultados: dict[str, Any] | None) -> dict[str, Any]:
    """As métricas do §16.4 no formato do painel: medidas, não medidas e as que faltam construir.

    `resultados` já vem carregado (não abre arquivo aqui) para que
    `exportador.py` leia uma vez só e reuse o mesmo payload no `inventario.json`
    e no `painel.html` — dois artefatos, uma leitura, sem risco de
    inconsistência entre os dois.
    """
    vereditos: dict[str, Any] = (resultados or {}).get("vereditos", {})
    medidas: dict[str, Any] = {
        "recall_5": vereditos.get("recall_5", "nao_medido"),
        "faithfulness_citacao": vereditos.get("faithfulness_citacao", "nao_medido"),
        "faithfulness_juiz": vereditos.get("faithfulness_juiz", "nao_medido"),
        "relevancia_juiz": vereditos.get("relevancia_juiz", "nao_medido"),
        "taxa_erro_tool": vereditos.get("taxa_erro_tool", "nao_medido"),
    }
    nao_implementadas = {
        nome: {"status": "nao_implementado", "motivo": motivo}
        for nome, motivo in _NAO_IMPLEMENTADAS.items()
    }
    return {
        "medido_em": (resultados or {}).get("medido_em", ""),
        "commit": (resultados or {}).get("commit", ""),
        "camadas_medidas": (resultados or {}).get("camadas", []),
        "metricas": {**medidas, **nao_implementadas},
    }


# --- ROI (§16.6) ---------------------------------------------------------

# **Premissa declarada, não medição.** O tempo de busca manual em norma não
# tem instrumentação neste projeto — é uma estimativa razoável, e dizer isso
# no próprio dado é o que separa engenharia de propaganda. Ver o cabeçalho
# do §16.6: "premissa disfarçada de número contradiz tudo o que as Fases 3 e
# 7 defendem".
PREMISSA_MINUTOS_BUSCA_MANUAL = 8.0


def roi_estimado(resultados: dict[str, Any] | None) -> dict[str, Any]:
    """O ganho de tempo por consulta — rotulado como premissa, nunca como fato."""
    return {
        "tipo": "premissa_declarada",
        "minutos_busca_manual_estimados": PREMISSA_MINUTOS_BUSCA_MANUAL,
        # Número sem procedência não vale nada (regra de `observabilidade.md`):
        # a premissa não muda por execução, mas fica datada pelo mesmo commit
        # que mediu o resto do painel, para não parecer solta no tempo.
        "referencia": {
            "commit": (resultados or {}).get("commit", ""),
            "medido_em": (resultados or {}).get("medido_em", ""),
        },
        "custo_por_consulta": {
            "status": "nao_implementado",
            "motivo": _NAO_IMPLEMENTADAS["custo_por_consulta"],
        },
        "nota": (
            "o tempo de busca manual é estimativa declarada, não medição; o custo real por "
            "consulta viria do Langfuse e não está implementado nesta fase — ver "
            "docs/GOVERNANCA_IA.md"
        ),
    }
