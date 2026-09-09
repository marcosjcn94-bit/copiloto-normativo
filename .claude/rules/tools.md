---
paths: ["src/copiloto/tools/**"]
---

# Regras das tools

## São exatamente três

`buscar_normativo`, `consultar_catalogo`, `registrar_crm`. Continuam três depois da Fase 9 —
a governança é endpoint HTTP e artefato de CI, não tool do agente. Criar uma quarta exige
aprovação explícita.

## Contrato

Todo schema em `schemas.py`, Pydantic v2, `model_config = ConfigDict(extra='forbid')`.
Argumento não previsto é erro de validação, não campo ignorado — é isso que torna a
autocorreção de schema mensurável.

Entrada e saída tipadas. Nenhuma tool devolve `dict` solto.

## Leitura × escrita

`buscar_normativo` e `consultar_catalogo` são leitura: executam direto.

`registrar_crm` é **escrita**: nunca executa sozinha. Ela propõe o payload e o grafo
interrompe para aprovação humana (`interrupt()`, Fase 5). Toda chamada carrega chave de
idempotência — reenvio aprovado duas vezes não pode gerar dois registros.

## Erro

Falha de rede usa o backoff e o circuit breaker de `resiliencia.py`, nunca `try/except` local
improvisado. Erro é devolvido tipado ao grafo, não engolido: silenciar exceção aqui vira
alucinação lá na frente.

## Proibições

Sem efeito colateral escondido, sem estado global, sem chamada de LLM dentro de tool. Tool é
função determinística sobre entrada validada.
