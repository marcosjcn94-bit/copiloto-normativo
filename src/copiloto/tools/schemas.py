"""Contratos das três tools do §7 — Pydantic v2, `extra='forbid'`.

O contrato é a fronteira entre um modelo probabilístico e um sistema
determinístico. Quatro decisões sustentam isso, e todas valem defesa:

* **`extra='forbid'`.** Argumento não previsto reprova. Ignorar campo a mais
  esconde alucinação de parâmetro — o modelo inventa `filtro_avancado`, ninguém
  vê, e a busca sai sem o filtro que ele julgava ter aplicado.
* **`Literal` no que é fechado.** Tema e tipo de norma têm valores contáveis, e
  eles vão para o JSON Schema entregue ao modelo. Enumerar no schema previne o
  erro; validar depois só o detecta.
* **Regex nos `str`, `ge`/`le` nos números.** Um `k` de 10.000 não é pedido
  ambicioso, é um estouro de contexto e de custo.
* **Saída tipada.** Nenhuma tool devolve `dict` solto; o que sai daqui é o que a
  Fase 5 vai conferir citação por citação.

Os `Literal` de domínio (`Tema`, `TipoDeNorma`) precisam ser estáticos para
virar JSON Schema, então são a única duplicação do catálogo dentro do Python —
e `tests/test_tools.py` falha se eles divergirem do `SELECT DISTINCT` da tabela
`normas`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- vocabulário fechado do domínio -----------------------------------------

Tema = Literal[
    "computacao_em_nuvem",
    "continuidade_e_incidentes",
    "risco_operacional",
    "seguranca_cibernetica",
    "seguranca_da_informacao",
]

TipoDeNorma = Literal[
    "Circular",
    "Instrução Normativa BCB",
    "Resolução",
    "Resolução BCB",
    "Resolução CMN",
]

StatusVigencia = Literal["vigente", "revogada"]

# --- padrões de texto --------------------------------------------------------
# Rust regex (motor padrão do Pydantic v2): sem lookahead, sem backreference. E
# sem contador sobre classe unicode ampla — `[\w]{8,400}` estoura o limite de 10
# MB do compilador. Comprimento é `min_length`/`max_length`; o regex diz forma.

# Pergunta em linguagem natural: uma linha, sem caractere de controle e sem os
# delimitadores usados em marcação e injeção de prompt.
PADRAO_PERGUNTA = r"^[^\x00-\x1f<>{}\\|`]+$"

# `Resolução BCB nº 85, de 2021, art. 7º` — o mesmo formato que `Trecho.citacao`
# emite, inclusive o sufixo de revogação. O validador determinístico da Fase 5
# confere se a citação existe; aqui só se garante que ela tem forma de citação.
PADRAO_CITACAO = r"^[^\x00-\x1f]+, art\. \d{1,3}[º°]?( \(REVOGADA\))?$"

# Identificador de conversa do checkpointer (Fase 5) e do webhook (Fase 6).
PADRAO_THREAD = r"^[A-Za-z0-9_-]{8,64}$"

# Cliente no CRM: duas a quatro letras maiúsculas, hífen, quatro a oito dígitos.
PADRAO_CLIENTE = r"^[A-Z]{2,4}-\d{4,8}$"

# Número de norma como o BCB publica: dígitos, com ou sem separador de milhar.
PADRAO_NUMERO_NORMA = r"^\d{1,3}(\.\d{3})*$|^\d{1,6}$"

# Ano: 1988 é o piso do arcabouço regulatório vigente; o teto dá folga para
# norma publicada com vigência futura sem virar manutenção anual.
ANO_MINIMO = 1988
ANO_MAXIMO = 2100


class Contrato(BaseModel):
    """Base de todo schema de tool: fechado, imutável e sem espaço à toa."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# --- buscar_normativo --------------------------------------------------------


class EntradaBuscarNormativo(Contrato):
    """Argumentos da busca semântica por trecho de norma."""

    pergunta: Annotated[
        str,
        Field(
            min_length=8,
            max_length=400,
            pattern=PADRAO_PERGUNTA,
            description=(
                "Pergunta em linguagem natural sobre o conteúdo das normas. "
                "Não aceita número de norma isolado — para isso use consultar_catalogo."
            ),
        ),
    ]
    tema: Tema | None = Field(
        default=None,
        description="Restringe a busca a um tema do corpus. Omita para buscar em todos.",
    )
    apenas_vigentes: bool = Field(
        default=True,
        description=(
            "Quando verdadeiro, exclui normas revogadas. Use falso apenas se a pergunta "
            "for explicitamente sobre norma revogada ou sobre histórico."
        ),
    )
    k: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description=(
            "Quantos trechos retornar. Omita para usar o valor calibrado em "
            "config/parametros.toml; só informe se a pergunta exigir mais ou menos contexto."
        ),
    )


class TrechoCitado(Contrato):
    """Um artigo recuperado, pronto para ser citado — e conferido depois."""

    id: str
    id_norma: str
    norma: str
    artigo: str
    citacao: str
    texto: str
    score: float
    revogada: bool
    capitulo: str = ""
    secao: str = ""


class SaidaBuscarNormativo(Contrato):
    """Trechos recuperados nesta execução. Lista vazia é resposta legítima."""

    trechos: tuple[TrechoCitado, ...]
    total: int = Field(ge=0)
    ha_revogada: bool = Field(
        default=False,
        description="Verdadeiro se algum trecho vem de norma revogada — exige aviso na resposta.",
    )


# --- consultar_catalogo ------------------------------------------------------


class EntradaConsultarCatalogo(Contrato):
    """Filtros de metadado. Consulta SQL, não busca semântica."""

    tema: Tema | None = None
    tipo: TipoDeNorma | None = None
    numero: Annotated[str, Field(pattern=PADRAO_NUMERO_NORMA)] | None = Field(
        default=None,
        description="Número da norma, como publicado (ex.: '4.893' ou '85').",
    )
    status_vigencia: Literal["vigente", "revogada", "todas"] = Field(
        default="vigente",
        description="Filtro de vigência. O padrão exclui revogadas.",
    )
    ano_minimo: int | None = Field(default=None, ge=ANO_MINIMO, le=ANO_MAXIMO)
    ano_maximo: int | None = Field(default=None, ge=ANO_MINIMO, le=ANO_MAXIMO)
    limite: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Teto de resultados. Omita para usar o padrão de config/parametros.toml.",
    )


class NormaCatalogada(Contrato):
    """Uma linha da tabela `normas`. Metadado exato, sem similaridade no meio."""

    id_norma: str
    tipo: str
    numero: str
    ano: int
    data: str
    tema: str
    status_vigencia: StatusVigencia
    url: str
    titulo: str = ""
    ementa: str = ""
    artigos: int = Field(default=0, ge=0)


class SaidaConsultarCatalogo(Contrato):
    """Resultado completo e exato do filtro — é o ponto da tool ser SQL."""

    normas: tuple[NormaCatalogada, ...]
    total: int = Field(ge=0)
    truncado: bool = Field(
        default=False,
        description="Verdadeiro se o limite cortou resultados: a lista não é completa.",
    )


# --- registrar_crm -----------------------------------------------------------


class EntradaRegistrarCrm(Contrato):
    """Argumentos da proposta de registro. Escrita: passa por aprovação humana."""

    id_cliente: Annotated[
        str,
        Field(
            pattern=PADRAO_CLIENTE,
            description="Identificador do cliente no CRM, no formato 'ABC-12345'.",
        ),
    ]
    assunto: Annotated[str, Field(min_length=5, max_length=120, pattern=PADRAO_PERGUNTA)]
    resumo: Annotated[
        str,
        Field(
            min_length=20,
            max_length=2000,
            description=(
                "Resumo factual do que foi consultado e respondido. Não inclua "
                "parecer, recomendação nem afirmação de conformidade."
            ),
        ),
    ]
    normas_citadas: Annotated[
        tuple[Annotated[str, Field(min_length=8, max_length=160, pattern=PADRAO_CITACAO)], ...],
        Field(
            min_length=1,
            max_length=20,
            description=(
                "Citações exatamente como saíram de buscar_normativo "
                "(ex.: 'Resolução BCB nº 85, de 2021, art. 7º')."
            ),
        ),
    ]
    canal: Literal["chat", "email", "telefone", "webhook"] = "chat"


class PropostaDeRegistro(Contrato):
    """O que vai ao humano para aprovação, com a chave de idempotência pronta.

    A chave é derivada de `thread_id + passo + argumentos` e não do relógio: o
    modelo pode reemitir a chamada depois de um timeout, e duas propostas iguais
    têm de colidir na mesma chave em vez de virar dois registros.
    """

    entrada: EntradaRegistrarCrm
    thread_id: Annotated[str, Field(pattern=PADRAO_THREAD)]
    passo: int = Field(ge=1, le=99)
    idempotency_key: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class SaidaRegistrarCrm(Contrato):
    """Confirmação do CRM. `duplicado` é sucesso, não erro."""

    registrado: bool
    id_registro: str
    idempotency_key: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    duplicado: bool = False
