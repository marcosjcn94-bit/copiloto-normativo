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

## Escopo por norma restringe as duas pontas

Quando a busca é restrita a uma norma, o corte vai no vetorial (`where`) **e** no BM25
(`ids_permitidos`), antes do top-`k`. Restringir só um lado faz o RRF fundir um lado escopado
com outro que não é, e o resultado não é nem a busca ampla nem a escopada. Filtrar *depois*
do top-`k` é pior ainda: devolve só os artigos da norma que por acaso entraram no top-30 do
corpus inteiro, que é justamente o que já não estava acontecendo.

Corolário: `k` é orçamento de candidatos, não de resultado. Todo filtro que precise atuar
depois da recuperação paga sobrebusca explícita (`fator_sobrebusca`), nunca silenciosa.

## Ambiguidade não escopa

Número de norma que resolve para mais de uma norma não vira escopo. Busca ampla ainda pode
achar o artigo certo; busca escopada na norma errada não pode. O mesmo vale para norma
deduzida do texto que não existe no corpus: o palpite é descartado e a busca segue ampla.

A exceção é o número **pedido explicitamente** pelo modelo: aí a ausência é resposta, e a
tool devolve zero trechos em vez de cair para a busca ampla. Cair devolveria artigo de outra
norma para uma pergunta que nomeou a sua.

## Artigo cortado diz que foi cortado

`max_chars_trecho` corta artigo longo demais para o orçamento, e o corte é anunciado dentro
do próprio texto. Artigo truncado em silêncio é pior que artigo ausente: o modelo conclui
pela ausência do inciso que sumiu e nada no contexto o contradiz.

O corte prefere a última quebra de parágrafo antes do teto. Artigo do BCB é caput mais
incisos, e cortar no meio de um inciso entrega meia obrigação.
