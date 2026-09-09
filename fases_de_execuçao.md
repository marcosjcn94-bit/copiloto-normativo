## Fases de execução

Executar em ordem. **Não avançar de fase sem o critério de pronto verde.** Ao fim de cada
fase, commitar e reportar em até 5 linhas. **Uma fase por sessão** — é o que mantém o custo
de token baixo e o contexto limpo.

### Fase 0 — Setup

`venv`, dependências, `git init`, repo no GitHub (público, para o CI ser gratuito),
`CLAUDE.md` (§11), `.claude/rules/` com `paths`, hook do `ruff`.

**Pronto:** `python -c "import langgraph, fastembed, flashrank; print('ok')"` roda sem erro;
repo criado; `ruff check .` limpo.

### Fase 1 — Fatia fina (fail-fast)

Baixar **3 normativos apenas**. Extrair, quebrar por artigo, indexar no Chroma. Uma query.

**Pronto:** acentuação correta no texto extraído; o trecho retornado carrega o número do
artigo correto na metadata. *Se a acentuação vier quebrada ou o artigo não vier na metadata,
parar e corrigir — todo o resto depende disso.*

### Fase 2 — Corpus completo + catálogo SQL

Coleta com retry e backoff, idempotente (pula o que já baixou). Índice denso, índice BM25 e
tabela `normas` em SQL com `tipo, numero, ano, data, tema, status_vigencia, url`.

**Pronto:** `SELECT count(*) FROM normas` bate com o `corpus.toml`; ao menos uma norma
marcada como revogada; reexecutar o script não rebaixa nada.

### Fase 3 — Recuperação híbrida

Denso + BM25 → RRF → FlashRank. Montar `evals/golden.jsonl` com 40 perguntas de gabarito.

**Pronto — este é o critério mais importante do projeto:** uma **tabela de ablação** medida,
não afirmada:

| Configuração | recall@5 | MRR |
|---|---|---|
| Só vetorial | | |
| Híbrido (RRF) | | |
| Híbrido + re-ranking | | |

Se o re-ranking não melhorar o número, **dizer isso no README**. Resultado negativo honesto
vale mais que alegação sem medição.

### Fase 4 — Tools com contrato estrito

As três tools do §7. Pydantic com `extra='forbid'`, regex nos `str`, `ge`/`le` nos números,
`Literal` nas decisões fechadas, descrição dizendo o que a tool **não** faz.

**Pronto:** teste de auto-correção — argumento inválido devolve erro estruturado ao modelo, o
modelo corrige na segunda tentativa, e uma terceira falha aborta para fallback.

### Fase 5 — Grafo, HITL e guardrails

`StateGraph` com estado tipado, `max_steps = 8`, checkpointer, `interrupt()` antes do
`registrar_consulta_crm`. Guardrails de entrada e saída (§8).

**Pronto:** o grafo interrompe antes da escrita e espera; matar o processo e retomar continua
do checkpoint sem refazer as chamadas de LLM já pagas.

### Fase 6 — API, webhook e CRM falso

FastAPI com `/perguntar`, `/aprovar` e `/saude`. `crm_falso` em outro processo. Workflow n8n
que recebe webhook, chama o agente e posta o resultado no CRM.

**Pronto:** disparar o webhook do n8n produz um registro no CRM falso, com o trace visível.

### Fase 7 — Observabilidade, evals e CI

Langfuse instrumentando o grafo inteiro. `evals/rodar.py` com as três camadas do
`ENGENHARIA_DE_SISTEMAS_IA_ESPECIFICACAO.md` §4.2. GitHub Actions rodando lint, testes e a
camada 1 dos evals.

Todo trace do Langfuse carrega dois atributos extras: `id_sistema` (qual dos provedores do
§3.2 atendeu) e `versao_ficha`. São duas linhas em `tracing.py` e são o gancho que a Fase 9
consome — sem elas o custo agregado não pode ser atribuído por sistema, e o inventário perde
metade do valor. Fazer agora sai de graça; fazer depois exige reprocessar traces.

**Pronto:** suíte verde no CI; um trace no Langfuse mostrando latência, tokens e custo por
nó, com `id_sistema` e `versao_ficha` presentes; faithfulness ≥ 0,95 e taxa de erro de
validação de tool ≤ 0,02, ou a explicação escrita do porquê não bateu.

### Fase 8 — Deploy Azure + README de portfólio

Container Apps com scale-to-zero, secrets no Key Vault ou nos secrets do Container App,
`deploy.yml` publicando pelo GitHub Actions.

**Pronto:** endpoint público respondendo `/saude`; **custo confirmado em R$ 0 no portal do
Azure**; README legível por um recrutador em 3 minutos, contendo a tabela de ablação, a
tabela do §2 ("o que NÃO fazer") e a tabela de cobertura da vaga (§12).

### Fase 9 — Camada de governança de IA

Detalhada no §16. Ficha do sistema em `config/governanca.yaml`, módulo `governanca/`,
endpoints `/governanca/*`, painel estático e os dois documentos em `docs/`.

**Pronto:**

1. `GET /governanca/coerencia` responde 200 com zero divergências.
2. **A prova viva:** alterar `max_passos` em `parametros.toml` sem atualizar a ficha deixa o
   CI vermelho; reverter deixa verde. Registrar isso no README.
3. `painel.html` abre com a rede desligada e mostra os quatro sistemas do §16.1, com números
   vindos do último `evals/rodar.py` — nenhum valor digitado à mão.
4. `docs/GOVERNANCA_IA.md` e `docs/GABBI_READY.md` escritos, o segundo com o aviso explícito
   de contrato inferido.
5. `ruff check .` limpo e `pytest` verde.