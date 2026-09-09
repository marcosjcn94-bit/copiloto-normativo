# Tabela de ablação — recuperação híbrida

Gerada por `evals/ablacao.py`. **Não editar à mão**: se um número aqui não
veio de uma execução, a tabela perde a única coisa que a torna útil.

- Medido em: 2026-09-09
- Commit: `03b0943`
- Gabarito: `evals/golden.jsonl`, 32 perguntas do tipo `rag`
- k avaliado: 5
- Parâmetros: k_denso=30, k_esparso=30, k_rrf=60, score_minimo=0.5
- Embedding: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Re-ranking: `ms-marco-MiniLM-L-12-v2`

| Configuração | recall@5 | MRR |
|---|---|---|
| Só vetorial | 0.625 | 0.511 |
| Híbrido (RRF) | 0.781 | 0.569 |
| Híbrido + re-ranking | 0.844 | 0.673 |

## Perguntas sem acerto por configuração

- **Só vetorial**: g02, g05, g06, g08, g09, g12, g15, g16, g19, g21, g22, g30
- **Híbrido (RRF)**: g06, g08, g12, g15, g21, g23, g30
- **Híbrido + re-ranking**: g06, g08, g12, g23, g30

## Leitura

- Somar o BM25 ao vetorial melhorou o recall@5 em 0.156 (0.625 para 0.781); o MRR variou 0.057.
- Somar o re-ranking ao híbrido melhorou o recall@5 em 0.062 (0.781 para 0.844); o MRR variou 0.104.

Nenhuma configuração recupera g06, g08, g12, g30. São o alvo da
próxima rodada: o que falha nas três não é problema de ordenação — é o artigo
certo não estar entre os candidatos que a busca gerou.

## O modelo de re-ranking, e o que quase virou resultado negativo

A primeira medição desta fase deu recall@5 = 0,406 com re-ranking, contra 0,781 do
híbrido puro. A conclusão pronta seria «o re-ranking não ajuda neste corpus», e ela
estaria errada. O modelo era `ms-marco-MultiBERT-L-12`, multilíngue — a escolha
óbvia para corpus em português. Ele **satura**: os 50 candidatos saem com score
entre 0,993 e 0,999, um espalhamento de 0,006. Score saturado não ordena, e o efeito
não é neutro: o artigo correto de uma das perguntas caiu do 6º para o 43º lugar.

Trocado pelo `ms-marco-MiniLM-L-12-v2`, treinado em inglês, o espalhamento vai a
0,49 no mesmo conjunto — e a tabela acima é o resultado. O diagnóstico barato para
reavaliar essa escolha não é o recall: é medir o espalhamento dos scores antes de
rodar a ablação inteira.

## O que o `score_minimo` não decide

Varrido de 0,0 a 0,7, nenhum valor mudou recall@5 nem MRR. Sobre perguntas que têm
resposta no corpus, o corte é inerte — está em 0,5 por ser o valor do §13, não por
medição que o aprove. O trabalho real dele é descartar quando nada é relevante, e
isso quem exercita são as cinco perguntas de «não sei» do gabarito, avaliadas na
Fase 7.
