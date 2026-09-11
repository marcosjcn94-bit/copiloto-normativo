---
paths: ["src/copiloto/observabilidade/**", "evals/**"]
---

# Regras de observabilidade e avaliação

## Trace nunca derruba o copiloto

Falha de Langfuse — pacote ausente, chave errada, serviço fora do ar — vira `RastreadorNulo`
com `motivo` preenchido, nunca exceção. Um sistema que para de responder porque o coletor de
métricas caiu trocou o objetivo pela instrumentação. `GET /saude` publica o motivo: dizer
*por que* não há trace manda quem opera para o lugar certo; dizer só que não há, não.

Corolário: o `langfuse` é extra (`pip install -e ".[obs]"`), não dependência principal. A
suíte hermética e o CI não o instalam.

## Chave errada não pode virar rastreador ativo

O SDK não autentica ao construir: com credencial inválida ele sobe, engole todo span e não
reclama. Por isso há um `auth_check` no boot. Sem ele, `/saude` afirmaria `ativo: true` num
serviço que não exporta nada — e o modo de falha de observabilidade que importa é o silencioso.

## Todo trace carrega `id_sistema` e `versao_ficha`

São o gancho que a Fase 9 consome para atribuir custo por sistema. Emitir depois exigiria
reprocessar trace já gravado, que é o mesmo que não ter. Sem ficha declarada, o valor é
`nao-declarada` — declarar ausência é dado; inventar `1.0.0` não é.

## Tipo de observação não é enfeite

`generation` é o que carrega custo e tokens; `tool` é o que um avaliador LLM-as-a-judge
consegue mirar; `agent` é o que vira nó do agent graph; `guardrail` é o que permite medir
quanto o validador de saída reprova. Marcar tudo como `span` joga as quatro coisas fora.
Uma observação por chamada de tool, irmã da geração que a pediu — nunca um span único em
volta do laço, que só daria o total.

## O que vai e o que não vai para o trace

* **Vai:** a conversa inteira que o modelo viu, no formato de mensagem da OpenAI. É o que
  responde "que contexto o agente tinha quando decidiu".
* **Não vai:** a pergunta crua. Só a saneada, lida do estado *depois* da execução — o
  guardrail de entrada roda dentro do grafo, e o trace vai para um terceiro.
* **Não vai:** texto de norma no resumo de nó. Ali cabe contagem e nome; o conteúdo aparece
  um nível abaixo, onde significa alguma coisa.

## A régua mora em `evals/metricas.py`

`recall@5` na tabela de ablação e `recall@5` no relatório da Fase 7 têm de ser o mesmo número
calculado do mesmo jeito. Nenhum script recalcula métrica por conta própria.

## Verificação pulada não é verificação verde

`/data` e `/indices` são gitignored, então o CI não tem corpus. Toda verificação declara se
precisa dele e, faltando, aparece como **pulada com motivo** — nunca aprovada. Métrica não
medida vira `nao_medido`, não um número inventado. Um relatório que aprova o que não mediu
ensina a ignorar relatório.

## Número sem procedência não vale nada

Todo resultado gravado carrega commit, data, modelo e provedor. Faithfulness de 0,9 não
significa nada sem dizer de qual modelo — e comparar duas medições sem isso compara coisas
diferentes achando que compara a mesma.

## Medição longa publica enquanto roda

Varredura de parâmetros leva dezenas de minutos. Ela grava cada linha assim que a linha sai,
em vez de montar tudo e escrever no fim. A primeira execução da varredura da Fase 7 foi
interrompida no meio e perdeu 20 minutos de CPU inteiros, porque a saída estava represada num
`grep` do pipeline: execução longa que só publica no fim é execução que, interrompida, não
publica nada.

Corolário para quem chama: nunca passar saída de execução longa por `grep` ou `tail` sem
`--line-buffered`. O filtro engole o resultado parcial.

## Hipótese testada e reprovada fica escrita

Quando uma correção plausível é medida e não funciona, o resultado negativo vai para o
artefato junto com o número que o sustenta — ver o desempate por RRF em `evals/ablacao.md`.
Sem isso, a próxima pessoa (ou a próxima sessão) reimplementa a mesma ideia, paga o mesmo
custo e chega ao mesmo lugar. Resultado negativo medido é informação cara; descartá-lo é
jogar fora o que a medição comprou.

## Parâmetro só muda com a medição ao lado

Nenhum valor de `[recuperacao]` muda por intuição. Ou a varredura mostra ganho, ou o valor
fica onde está — e o comentário no TOML registra a medição que o manteve, não só a que o
moveu. Valor mantido sem número é valor não verificado, e o comentário precisa dizer isso.
