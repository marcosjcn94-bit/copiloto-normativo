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

## Despacho e autocorreção

`registro.py` é o único ponto que converte chamada crua do modelo em execução. Ele valida
com o Pydantic da tool, executa e devolve `ResultadoDeTool` — saída tipada ou `ErroDeTool`.
Nunca levanta exceção por culpa do modelo.

O erro que volta ao modelo é JSON com campo, valor recebido e valores permitidos. Mensagem
genérica não é erro estruturado: o modelo precisa saber **o que** corrigir, não que errou.

`max_autocorrecao` vem de `[grafo]` no TOML. Falha de schema concede nova tentativa; falha
de execução, não — argumento válido que quebrou na execução é problema do sistema, e
reformular o argumento só queima orçamento.
