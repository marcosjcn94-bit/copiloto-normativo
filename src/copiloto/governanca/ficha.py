"""A ficha do sistema — `config/governanca.yaml` tipado (§16.2 do briefing).

Organizada nas quatro funções do NIST AI RMF (GOVERN, MAP, MEASURE, MANAGE).
Pydantic v2 com `extra='forbid'` em todo modelo, a mesma disciplina de
contrato do §7: a ficha é um contrato tipado, não um documento solto. Campo
que o YAML não declara e que o Pydantic não conhece é erro de validação, não
dado ignorado em silêncio — é o que torna `coerencia.py` confiável.

**Por que YAML e não TOML, e por que isso é dependência nova.**
`observabilidade/tracing.py` já lê este arquivo, mas só um campo
(`identificacao.versao`), e por isso usa regex — carregar `pyyaml` para uma
string seria dependência sem necessidade. Aqui o documento inteiro entra
tipado, com listas e objetos aninhados (`modelos[]`, `controles[]`), e regex
não sustenta isso com segurança. `pyyaml` é a única dependência nova da Fase
9, aprovada por exceção à lista fechada do §3 — ver `pyproject.toml`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

RAIZ_PADRAO = Path(__file__).resolve().parents[3]
CAMINHO_PADRAO = RAIZ_PADRAO / "config" / "governanca.yaml"


class _Base(BaseModel):
    """Base dos modelos da ficha: fechado, como os contratos de tool do §7."""

    model_config = ConfigDict(extra="forbid")


# --- GOVERN --------------------------------------------------------------


class Identificacao(_Base):
    id: str
    nome: str
    versao: str
    dono: str
    contato: str
    data_registro: date


class Proposito(_Base):
    descricao: str
    dominio: str
    publico_alvo: str
    # Vêm prontos das §1 e §2: não interpreta norma, não dá parecer jurídico,
    # não afirma conformidade. Declarar como dado, não como texto solto no
    # README, é o que permite `/governanca/ficha` publicar o limite de escopo
    # com a mesma força de contrato que os campos técnicos.
    casos_de_uso_vedados: list[str]


class ClassificacaoDeRisco(_Base):
    nivel: Literal["risco_minimo", "risco_limitado", "risco_alto", "risco_inaceitavel"]
    justificativa: str
    framework_referencia: str


class Autonomia(_Base):
    nivel: int = Field(ge=0, le=4)
    acoes_autonomas: list[str]
    acoes_que_exigem_aprovacao: list[str]


# --- MAP -------------------------------------------------------------------


class ModeloDeIA(_Base):
    """Uma entrada da frota. `implementado=False` é dado, não omissão.

    `azure_openai` e `ollama` estão previstos no §5/§3.2 e ainda não existem
    no repositório — `llm/provedor.py` documenta isso e
    `api/main.py::provedor_do_ambiente` falha alto se alguém tentar usá-los.
    Declará-los aqui com `implementado=False` é o que deixa
    `governanca/inventario.py` cruzar a ficha com o código sem inventar
    entrada nem esconder o que falta: `coerencia.py` só cobra presença no
    inventário real de quem está marcado `implementado=True`.
    """

    id_sistema: str
    provedor: str
    modelo: str
    fornecedor: str
    hospedagem: Literal["cloud_terceiro", "local"]
    papel: Literal["primario", "fallback", "juiz"]
    implementado: bool


class DadosDoSistema(_Base):
    corpus: str
    pii: str
    base_legal: str
    retencao: str


# --- MEASURE -----------------------------------------------------------------


class LimitesOperacionais(_Base):
    """Espelha `[grafo]` e `[llm]` de `parametros.toml`. `coerencia.py` confere."""

    max_passos: int
    max_autocorrecao: int
    max_tentativas_resposta: int
    temperatura: float
    max_tokens_ctx: int


class Controle(_Base):
    id: str
    tipo: Literal["entrada", "saida"]
    deterministico: bool
    # Caminho de teste (arquivo ou nodeid do pytest) que `coerencia.py`
    # confere existir e passar. Controle sem prova é afirmação, não controle.
    teste: str


class Metricas(_Base):
    """Os thresholds do §13. `onde_medido` mapeia métrica → o que a mede."""

    faithfulness_min: float
    relevancia_min: float
    erro_tool_max: float
    recall_5_min: float
    onde_medido: dict[str, str]


# --- MANAGE ------------------------------------------------------------------


class Incidentes(_Base):
    fallback: str


class CicloDeVida(_Base):
    status: Literal["ativo", "em_revisao", "descontinuado"]
    data_ultima_avaliacao: date
    gatilhos_reavaliacao: list[str]


class FichaDoSistema(_Base):
    identificacao: Identificacao
    proposito: Proposito
    classificacao_risco: ClassificacaoDeRisco
    autonomia: Autonomia
    modelos: list[ModeloDeIA]
    dados: DadosDoSistema
    limites_operacionais: LimitesOperacionais
    controles: list[Controle]
    metricas: Metricas
    incidentes: Incidentes
    ciclo_de_vida: CicloDeVida


def carregar_ficha(caminho: Path = CAMINHO_PADRAO) -> FichaDoSistema:
    """Lê e valida `config/governanca.yaml`. Levanta se o arquivo faltar ou mentir.

    Falhar alto é a escolha: uma ficha ausente ou malformada não pode virar
    `/governanca/ficha` 200 com dado inventado. Quem chama decide o status
    HTTP — este módulo só valida.
    """
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    return FichaDoSistema.model_validate(bruto)
