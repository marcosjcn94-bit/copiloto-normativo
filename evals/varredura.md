# Varredura de parâmetros de recuperação

Gerada por `evals/ablacao.py --varredura`. **Não editar à mão.**

- Medido em: 2026-09-10
- Commit: `2ad92a7+sujo`
- Configuração: híbrido + re-ranking + escopo por norma, avaliado em k=5
- Re-ranking: `ms-marco-MiniLM-L-12-v2`

| k candidatos | score_minimo | recall@5 | MRR | s/pergunta | sem acerto |
|---|---|---|---|---|---|
| 30 | 0.0 | 0.844 | 0.694 | 6.2 | g06, g08, g12, g23, g30 |
| 30 | 0.1 | 0.844 | 0.694 | 6.2 | g06, g08, g12, g23, g30 |
| 30 | 0.3 | 0.844 | 0.694 | 6.2 | g06, g08, g12, g23, g30 |
| 30 | 0.5 | 0.844 | 0.694 | 6.2 | g06, g08, g12, g23, g30 |
| 60 | 0.0 | 0.812 | 0.647 | 11.8 | g06, g12, g20, g22, g23, g30 |
| 60 | 0.1 | 0.812 | 0.647 | 11.8 | g06, g12, g20, g22, g23, g30 |
| 60 | 0.3 | 0.812 | 0.647 | 11.8 | g06, g12, g20, g22, g23, g30 |
| 60 | 0.5 | 0.812 | 0.647 | 11.8 | g06, g12, g20, g22, g23, g30 |
| 100 | 0.0 | 0.844 | 0.689 | 20.0 | g06, g12, g20, g23, g30 |
| 100 | 0.1 | 0.844 | 0.689 | 20.0 | g06, g12, g20, g23, g30 |
| 100 | 0.3 | 0.844 | 0.689 | 20.0 | g06, g12, g20, g23, g30 |
| 100 | 0.5 | 0.844 | 0.689 | 20.0 | g06, g12, g20, g23, g30 |
| 150 | 0.0 | 0.875 | 0.696 | 33.8 | g06, g12, g23, g30 |
| 150 | 0.1 | 0.875 | 0.696 | 33.8 | g06, g12, g23, g30 |
| 150 | 0.3 | 0.875 | 0.696 | 33.8 | g06, g12, g23, g30 |
| 150 | 0.5 | 0.875 | 0.696 | 33.8 | g06, g12, g23, g30 |

## Como ler

As linhas de mesma largura compartilham o passe de re-ranking: `score_minimo` é
filtro sobre a lista já ordenada, então limiar diferente não refaz a busca.

Se o `recall` não se move ao longo dos limiares de uma mesma largura, o corte não
está decidindo o top-k — o que ele elimina já estava abaixo dele. Isso **não**
quer dizer que o corte é inofensivo: ele decide quantos artigos chegam ao modelo,
e essa quantidade não aparece nesta tabela.

Se o `recall` não sobe ao longo das larguras, o gargalo não é quantos candidatos
a busca gera, e sim o re-ranking saber ordená-los.

