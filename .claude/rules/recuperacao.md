---
paths: ["src/copiloto/recuperacao/**"]
---

# Regras da camada de recuperação

## Pipeline fixo

`denso` + `esparso` → `fusao` (RRF) → `rerank` (FlashRank) → `k_final` trechos.

Denso e esparso são **complementares por necessidade do domínio**: embedding não distingue
`4.658` de `4.568` (isso é BM25); "requisitos para contratação de nuvem" não é atendido por
termo exato (isso é vetorial). Nenhum dos dois pode ser removido sem número que justifique.

## Parâmetros

Todos vêm de `config/parametros.toml`, seção `[recuperacao]`. Nenhum literal numérico de
`k`, threshold ou peso de fusão dentro do Python — se precisou de um número novo, ele nasce
no TOML.

Os valores iniciais são ponto de partida, não verdade. A tabela de ablação da Fase 3 define
os finais. Se a medição contradisser o TOML, vale a medição — e a mudança vai para o README.

## Adapter vetorial

`adapters/base.py` define um `Protocol`. `chroma.py` e `azure_ai_search.py` implementam.
Nenhum módulo fora de `adapters/` importa `chromadb` diretamente — quem quiser vetorial pede
o Protocol. É esse desacoplamento que prova a arquitetura.

## Modelos ONNX

Um único modelo carregado por vez. `fastembed` e `FlashRank` compartilham `onnxruntime` na
variante CPU. Carregar sob demanda e reutilizar a instância; nunca instanciar dentro de laço.

## Medição

Toda mudança de recuperação é acompanhada de `recall@k` e `MRR` sobre `evals/golden.jsonl`.
"Achei que ficou melhor" não é resultado. Rodar sobre os 3 normativos da Fase 1 durante o
desenvolvimento; corpus completo só com a amostra verde.

## Contexto

Nunca imprimir chunk, texto de norma ou vetor no chat. Reportar contagem, IDs ou métricas.
