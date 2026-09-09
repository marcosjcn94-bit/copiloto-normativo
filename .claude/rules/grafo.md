---
paths: ["src/copiloto/grafo/**"]
---

# Regras do grafo

## Autonomia nível 2

O agente lê e propõe; **toda mutação externa exige confirmação humana**. Não interpreta a
norma, não emite parecer, não afirma conformidade. Localiza, cita e mostra a origem.

## Estado

`estado.py` define um `AgentState` tipado (`TypedDict`). Nenhum nó adiciona chave que não
esteja declarada. Nó é função pura de estado para atualização parcial de estado.

## Limites rígidos

`max_passos` e `max_autocorrecao` vêm de `config/parametros.toml`, seção `[grafo]`. São
limite de segurança, não sugestão: estourou, o grafo termina com fallback explícito — nunca
continua "só mais uma vez". Mudar o valor no TOML tem que ser suficiente para mudar o
comportamento, sem tocar em Python.

## HITL

A aprovação usa `interrupt()` do LangGraph com checkpointer, não flag booleana nem espera
ativa. O estado tem que sobreviver ao reinício do processo: retomar do checkpoint é o que
prova que o HITL é real.

## Guardrails

`guardrails.py` roda na entrada e na saída. O **validador de citação** é obrigatório e
determinístico: se a resposta cita artigo que não está nos trechos recuperados, a resposta é
bloqueada. Isso é código, não prompt — prompt não é garantia.

## Proibições

Sem prompt que "peça" para o modelo se comportar quando existe verificação possível em
código. Sem nó que chame outro nó direto — quem decide caminho é a aresta condicional.
