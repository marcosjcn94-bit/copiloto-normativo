# Relatório de avaliação

Gerado por `evals/rodar.py`. **Não editar à mão** — o próximo `rodar.py` sobrescreve,
e um número editado aqui deixa de ter execução por trás.

- Medido em: 2026-09-11T10:10:35+00:00
- Commit: `5ac54ab`
- Camadas pedidas: 1
- Corpus disponível: sim
- `versao_ficha`: `nao-declarada`

## Verificações

| Camada | Verificação | Situação | Falhas |
|---|---|---|---|
| 1 | `c1.contratos` | verde | 0 |
| 1 | `c1.prompt_cita_tools_reais` | verde | 0 |
| 1 | `c1.orcamento_fixo` | verde | 0 |
| 1 | `c1.orcamento_real` | verde | 0 |
| 1 | `c1.tools_respondem` | verde | 0 |
| 1 | `c1.citacao_no_contexto` | verde | 0 |

## Métricas contra os thresholds de `parametros.toml`

| Métrica | Medido | Exigido | Veredito |
|---|---|---|---|
| `recall_5` | — | — | não medido nesta execução |
| `faithfulness_citacao` | — | — | não medido nesta execução |
| `taxa_erro_tool` | — | — | não medido nesta execução |

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
    "tokens_prompt": 284,
    "tokens_schemas": 1214,
    "tokens_maior_pergunta": 40,
    "tokens_fixos": 1538,
    "max_tokens_ctx": 8000,
    "folga_para_trechos": 6462,
    "estimativa": "~4 caracteres por token, não tokenizador real"
  },
  "c1.orcamento_real": {
    "tokens_fixos": 1498,
    "pior_caso": 3562,
    "media": 3200,
    "max_tokens_ctx": 8000,
    "k_final_avaliado": "o padrão de config/parametros.toml",
    "nota": "o free tier da Groq limita 8000 tokens/minuto somados a TODAS as chamadas; uma pergunta gasta várias voltas do agente",
    "estimativa": "~4 caracteres por token, não tokenizador real"
  },
  "c1.tools_respondem": {
    "normas_no_catalogo": 20
  }
}
```
