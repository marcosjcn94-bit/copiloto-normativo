"""Assinatura do encoder que gerou o índice denso.

O nome do modelo não basta para identificar um espaço vetorial: o `fastembed`
trocou o pooling de `paraphrase-multilingual-MiniLM-L12-v2` de CLS para média
entre 0.5 e 0.8 sem mudar o nome do modelo. Índice antigo com biblioteca nova dá
vetores de consulta incomparáveis com os indexados — e o sintoma é recuperação
pior, não exceção. Por isso a indexação deixa aqui quem a produziu, e a
recuperação confere antes de abrir.

Mora na raiz do pacote, e não em `recuperacao/`, porque `ingestao` também
depende dela e a ingestão não deve importar a recuperação.
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import version
from pathlib import Path

logger = logging.getLogger(__name__)

NOME_DO_ARQUIVO = "assinatura_densa.json"


class IndiceDesatualizado(RuntimeError):
    """O índice denso foi gerado por um encoder diferente do que vai consultar."""


def assinatura_atual(modelo: str) -> dict[str, str]:
    """Quem produziria os vetores agora: nome do modelo e versão da biblioteca."""
    return {"modelo": modelo, "fastembed": version("fastembed")}


def gravar_assinatura(caminho: Path, *, modelo: str) -> None:
    """Chamada pela indexação, ao lado do índice que acabou de escrever."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(assinatura_atual(modelo), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def conferir_assinatura(caminho: Path, *, modelo: str) -> None:
    """Levanta `IndiceDesatualizado` se o índice não combina com o encoder atual.

    Arquivo ausente apenas avisa: índice gerado antes desta checagem existir é
    inconclusivo, não errado, e falhar nele inutilizaria índices legítimos.
    """
    if not caminho.exists():
        logger.warning(
            "indice denso sem assinatura — nao da para conferir o encoder",
            extra={"caminho": str(caminho)},
        )
        return

    gravada = json.loads(caminho.read_text(encoding="utf-8"))
    atual = assinatura_atual(modelo)
    divergencias = [chave for chave, valor in atual.items() if gravada.get(chave) != valor]
    if divergencias:
        raise IndiceDesatualizado(
            "indice denso gerado com "
            + ", ".join(f"{c}={gravada.get(c)!r}" for c in divergencias)
            + "; agora seria "
            + ", ".join(f"{c}={atual[c]!r}" for c in divergencias)
            + " — reindexe antes de consultar"
        )
