# Relatório de avaliação

Gerado por `evals/rodar.py`. **Não editar à mão** — o próximo `rodar.py` sobrescreve,
e um número editado aqui deixa de ter execução por trás.

- Medido em: 2026-09-10T19:56:00+00:00
- Commit: `47250dd`
- Camadas pedidas: 1, 2, 3
- Corpus disponível: sim
- `versao_ficha`: `nao-declarada`

## Verificações

| Camada | Verificação | Situação | Falhas |
|---|---|---|---|
| 1 | `c1.contratos` | verde | 0 |
| 1 | `c1.prompt_cita_tools_reais` | verde | 0 |
| 1 | `c1.orcamento_fixo` | verde | 0 |
| 1 | `c1.orcamento_real` | **VERMELHA** | 1 |
| 1 | `c1.tools_respondem` | verde | 0 |
| 1 | `c1.citacao_no_contexto` | verde | 0 |
| 2 | `c2.recuperacao` | verde | 0 |
| 3 | `c3.agente` | verde | 0 |

## Métricas contra os thresholds de `parametros.toml`

| Métrica | Medido | Exigido | Veredito |
|---|---|---|---|
| `recall_5` | 0.8438 | ≥ 0.8 | verde |
| `faithfulness_citacao` | 1.0 | ≥ 0.95 | verde |
| `taxa_erro_tool` | 0.0 | ≤ 0.02 | verde |
| `faithfulness_juiz` | 1.0 | ≥ 0.95 | verde |
| `relevancia_juiz` | 1.0 | ≥ 0.85 | verde |

## O que esta execução não mediu

- 3 de 8 perguntas da camada 3 não foram medidas: o free tier da Groq recusou a chamada (HTTP 429/413, teto de 8.000 tokens/minuto). Toda métrica de camada 3 vale sobre n=5.
- 1 evidência(s) chegaram truncadas ao juiz por causa do mesmo teto. Onde o corte morde, `faithfulness_juiz` mede o corte, não o agente — a nota determinística `faithfulness_citacao` não sofre disso.
- n=5 é amostra pequena demais para os thresholds do §13 serem conclusivos; eles são reportados, não afirmados.
- o orçamento real de contexto estoura no pior caso (14932 tokens contra 8000): há perguntas que o free tier não consegue responder sem baixar `k_final` ou truncar o texto do trecho.

### Falhas em `c1.orcamento_real`

- g02: 14932 tokens > max_tokens_ctx 8000

## Detalhe medido

```json
{
  "c1.prompt_cita_tools_reais": {
    "tools_registradas": [
      "buscar_normativo",
      "consultar_catalogo",
      "registrar_crm"
    ]
  },
  "c1.orcamento_fixo": {
    "tokens_prompt": 224,
    "tokens_schemas": 1011,
    "tokens_maior_pergunta": 40,
    "tokens_fixos": 1275,
    "max_tokens_ctx": 8000,
    "folga_para_trechos": 6725,
    "estimativa": "~4 caracteres por token, não tokenizador real"
  },
  "c1.orcamento_real": {
    "tokens_fixos": 1235,
    "pior_caso": 14932,
    "media": 6049,
    "max_tokens_ctx": 8000,
    "k_final_avaliado": "o padrão de config/parametros.toml",
    "nota": "o free tier da Groq limita 8000 tokens/minuto somados a TODAS as chamadas; uma pergunta gasta várias voltas do agente",
    "estimativa": "~4 caracteres por token, não tokenizador real"
  },
  "c1.tools_respondem": {
    "normas_no_catalogo": 20
  },
  "c2.recuperacao": {
    "k": 5,
    "recall": 0.8438,
    "mrr": 0.6729,
    "sem_acerto": [
      "g06",
      "g08",
      "g12",
      "g23",
      "g30"
    ]
  },
  "c3.agente": {
    "amostra": [
      "g01",
      "g02",
      "g09",
      "g21",
      "g26",
      "g33",
      "g38",
      "g39"
    ],
    "medidas": 5,
    "indisponibilidades": [
      "g01: groq respondeu 429",
      "g21: groq recusou o pedido: 413",
      "g26: groq recusou o pedido: 413"
    ],
    "faithfulness_citacao": 1.0,
    "faithfulness_juiz": 1.0,
    "relevancia_juiz": 1.0,
    "juiz": "eval-judge/camada-3",
    "notas_do_juiz": 10,
    "evidencias_truncadas": 1,
    "vereditos_do_juiz": [
      {
        "pergunta": "g02",
        "criterio": "faithfulness",
        "valor": 1.0,
        "justificativa": "A resposta indica corretamente que o prazo é de cinco anos, conforme o artigo 23 da Resolução CMN 4893/2021, e menciona que a norma está vigente, informação presente na evidência."
      },
      {
        "pergunta": "g02",
        "criterio": "relevancia",
        "valor": 1.0,
        "justificativa": "A resposta indica corretamente que o prazo é de cinco anos, conforme o artigo 23 da Resolução CMN 4893/2021, e menciona que a norma está vigente, informação presente na evidência."
      },
      {
        "pergunta": "g09",
        "criterio": "faithfulness",
        "valor": 1.0,
        "justificativa": "A resposta afirma que não há norma que exija comunicação da contratação, o que está de acordo com os trechos apresentados, que não mencionam tal obrigação, e responde diretamente à pergunta."
      },
      {
        "pergunta": "g09",
        "criterio": "relevancia",
        "valor": 1.0,
        "justificativa": "A resposta afirma que não há norma que exija comunicação da contratação, o que está de acordo com os trechos apresentados, que não mencionam tal obrigação, e responde diretamente à pergunta."
      },
      {
        "pergunta": "g33",
        "criterio": "faithfulness",
        "valor": 1.0,
        "justificativa": "A resposta indica falta de base normativa, o que está de acordo com a ausência de trechos nas evidências fornecidas."
      },
      {
        "pergunta": "g33",
        "criterio": "relevancia",
        "valor": 1.0,
        "justificativa": "A resposta indica falta de base normativa, o que está de acordo com a ausência de trechos nas evidências fornecidas."
      },
      {
        "pergunta": "g38",
        "criterio": "faithfulness",
        "valor": 1.0,
        "justificativa": "A resposta lista a única norma sobre computação em nuvem que consta como revogada na evidência, sem acrescentar informações não suportadas."
      },
      {
        "pergunta": "g38",
        "criterio": "relevancia",
        "valor": 1.0,
        "justificativa": "A resposta lista a única norma sobre computação em nuvem que consta como revogada na evidência, sem acrescentar informações não suportadas."
      },
      {
        "pergunta": "g39",
        "criterio": "faithfulness",
        "valor": 1.0,
        "justificativa": "A resposta indica corretamente que há 5 normativos vigentes, conforme os dados do catálogo fornecido."
      },
      {
        "pergunta": "g39",
        "criterio": "relevancia",
        "valor": 1.0,
        "justificativa": "A resposta indica corretamente que há 5 normativos vigentes, conforme os dados do catálogo fornecido."
      }
    ],
    "media_geral_do_juiz": 1.0,
    "chamadas_de_tool": 12,
    "chamadas_recusadas_no_contrato": 0,
    "taxa_erro_tool": 0.0,
    "acuracia_escolha_de_tool": 1.0,
    "recusas_corretas_em_nao_sei": "1/1",
    "id_sistema": "groq",
    "modelo": "openai/gpt-oss-120b",
    "id_sistema_juiz": "eval-judge/camada-3",
    "modelo_juiz": "openai/gpt-oss-120b"
  }
}
```
