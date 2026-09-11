"""A frota real, varrendo o repositório (§16.1 do briefing).

**Nenhuma entrada fictícia, nenhuma fixture** — a regra do §1.4 contra dado
sintético vale aqui como vale no corpus. Duas fontes, e as duas são código
que já existe por outro motivo, não uma lista digitada para esta fase:

1. `llm/*.py` — cada módulo é inspecionado por `issubclass(classe,
   ProvedorLLM)`, o `runtime_checkable Protocol` de `llm/provedor.py`. Uma
   classe entra no inventário porque **implementa** o contrato, não porque
   alguém a citou aqui.
2. `evals/rodar.py::ID_SISTEMA_JUIZ` — o quarto sistema do §16.1, "o LLM que
   julga o outro LLM". Ele não mora em `llm/` porque não atende ao cliente;
   é um `ProvedorLLM` decorado dentro de `Juiz`, e sua identidade sai
   diretamente da constante que o script já usa para etiquetar o trace.

O achado do §16.1 se confirma aqui, medido e não afirmado: hoje `llm/`
contém só `groq.py`. `azure_openai.py` e `ollama.py` estão previstos no
§5/§3.2 e ainda não existem — `provedor_do_ambiente` falha alto se alguém os
pedir (ver `api/main.py`). A frota real varrida é **2 sistemas**, não os 4
do desenho original; os outros 2 aparecem no inventário como "previstos, não
implementados", lidos de `config/governanca.yaml::modelos[]`, nunca
inventados aqui.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path

from copiloto.governanca.ficha import ModeloDeIA

logger = logging.getLogger(__name__)

RAIZ_PADRAO = Path(__file__).resolve().parents[3]

# Módulos de `llm/` que não são implementação de provedor: o contrato em si
# e o pacote. Varrer estes dois produziria zero classes e nenhum ruído, mas
# excluí-los explicitamente é mais barato que depender de `issubclass`
# falhar em silêncio se um deles um dia ganhar uma classe com `gerar`.
_NAO_PROVEDORES = {"__init__", "provedor"}


@dataclass(frozen=True, slots=True)
class SistemaDeIA:
    """Uma linha da frota — real (varrida) ou prevista (declarada na ficha)."""

    id_sistema: str
    papel: str
    origem: str
    implementado: bool


def _cumpre_provedor(classe: type) -> bool:
    """Estrutura mínima de `ProvedorLLM`: `id_sistema` fixo e `gerar` chamável.

    Não é `issubclass(classe, ProvedorLLM)`: o Protocol declara `id_sistema`
    como atributo, não só método, e um "data protocol" desses não suporta
    `issubclass()` em tempo de execução — levanta `TypeError`. `isinstance()`
    funcionaria, mas exigiria instanciar cada provedor só para inspecionar a
    classe, o que puxaria credencial (`GROQ_API_KEY`) para dentro do
    inventário. A checagem manual é o que sobra sem pagar nenhum dos dois.
    """
    return isinstance(getattr(classe, "id_sistema", None), str) and callable(
        getattr(classe, "gerar", None)
    )


def _classes_de_provedor(raiz: Path) -> list[type]:
    """Toda classe em `llm/*.py` que cumpre `ProvedorLLM` por estrutura."""
    pasta_llm = raiz / "src" / "copiloto" / "llm"
    encontradas: list[type] = []
    for arquivo in sorted(pasta_llm.glob("*.py")):
        nome = arquivo.stem
        if nome in _NAO_PROVEDORES:
            continue
        modulo = importlib.import_module(f"copiloto.llm.{nome}")
        for _, classe in inspect.getmembers(modulo, inspect.isclass):
            if classe.__module__ != modulo.__name__:
                continue  # importado de outro lugar, não definido aqui
            if _cumpre_provedor(classe):
                encontradas.append(classe)
    return encontradas


def _id_sistema_da_classe(classe: type) -> str | None:
    """O `id_sistema` declarado na classe, sem instanciar.

    Instanciar exigiria credencial (`GROQ_API_KEY`) só para ler um atributo de
    classe — e o inventário não pode depender de segredo estar configurado.
    """
    valor = getattr(classe, "id_sistema", None)
    return valor if isinstance(valor, str) and valor else None


def frota_real(raiz: Path = RAIZ_PADRAO) -> tuple[SistemaDeIA, ...]:
    """Os sistemas de IA que o código hoje implementa, varrendo `llm/` e o juiz."""
    sistemas: list[SistemaDeIA] = []
    for classe in _classes_de_provedor(raiz):
        id_sistema = _id_sistema_da_classe(classe)
        if id_sistema is None:
            logger.warning(
                "classe cumpre ProvedorLLM sem id_sistema fixo", extra={"classe": classe.__name__}
            )
            continue
        sistemas.append(
            SistemaDeIA(
                id_sistema=id_sistema,
                papel="primario",
                origem=f"{classe.__module__}.{classe.__name__}",
                implementado=True,
            )
        )

    from evals.rodar import ID_SISTEMA_JUIZ

    sistemas.append(
        SistemaDeIA(
            id_sistema=ID_SISTEMA_JUIZ,
            papel="juiz",
            origem="evals.rodar.Juiz",
            implementado=True,
        )
    )
    return tuple(sistemas)


def frota_prevista(modelos_da_ficha: tuple[ModeloDeIA, ...]) -> tuple[SistemaDeIA, ...]:
    """Os modelos que a ficha declara e o código ainda não implementa."""
    return tuple(
        SistemaDeIA(
            id_sistema=m.id_sistema,
            papel=m.papel,
            origem="config/governanca.yaml",
            implementado=False,
        )
        for m in modelos_da_ficha
        if not m.implementado
    )


def inventario(raiz: Path = RAIZ_PADRAO, modelos_da_ficha: tuple[ModeloDeIA, ...] = ()) -> dict:
    """A frota completa: real + prevista, em formato pronto para JSON."""
    real = frota_real(raiz)
    prevista = frota_prevista(modelos_da_ficha)
    return {
        "sistemas": [
            {
                "id_sistema": s.id_sistema,
                "papel": s.papel,
                "origem": s.origem,
                "implementado": s.implementado,
            }
            for s in (*real, *prevista)
        ],
        "total_implementados": len(real),
        "total_previstos": len(prevista),
    }
