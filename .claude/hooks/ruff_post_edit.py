"""Hook PostToolUse: roda ruff no arquivo Python recem-editado.

Formata, aplica correcoes seguras e devolve o que sobrou como feedback.
Silencioso quando o arquivo nao e Python ou quando o ruff nao esta instalado.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _python_do_venv() -> Path:
    for rel in ("Scripts/python.exe", "bin/python"):
        candidato = RAIZ / ".venv" / rel
        if candidato.exists():
            return candidato
    return Path(sys.executable)


def main() -> int:
    try:
        evento = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    caminho = (evento.get("tool_input") or {}).get("file_path", "")
    if not caminho.endswith(".py"):
        return 0

    alvo = Path(caminho)
    if not alvo.exists():
        return 0

    python = str(_python_do_venv())
    subprocess.run([python, "-m", "ruff", "format", str(alvo)], capture_output=True, cwd=RAIZ)
    subprocess.run(
        [python, "-m", "ruff", "check", "--fix", str(alvo)], capture_output=True, cwd=RAIZ
    )
    restante = subprocess.run(
        [python, "-m", "ruff", "check", str(alvo)], capture_output=True, text=True, cwd=RAIZ
    )

    if restante.returncode not in (0, 1):
        return 0
    if restante.returncode == 1 and restante.stdout.strip():
        print(f"ruff ainda aponta problemas em {alvo.name}:\n{restante.stdout}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
