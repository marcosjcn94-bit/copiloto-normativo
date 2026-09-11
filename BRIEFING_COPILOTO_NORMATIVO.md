# Copiloto Normativo BCB — Briefing de Execução

> Documento de instrução para o Claude Code. Leia inteiro antes de escrever qualquer linha.
> Em caso de dúvida entre duas implementações, escolha a mais simples e pergunte antes de
> introduzir dependência nova.
>
> Projeto de portfólio. O leitor final é um avaliador técnico, não um usuário. Cada decisão
> precisa ter justificativa escrita — inclusive as decisões de **não** fazer algo.

---

## 1. Objetivo

Um copiloto que responde perguntas sobre normas do Banco Central citando **norma, artigo e
status de vigência**, com a citação verificada por código antes de chegar ao usuário.

O agente recebe a pergunta por webhook (n8n) ou por API REST, recupera os trechos relevantes
com busca híbrida e re-ranking, consulta o catálogo de normas em SQL para saber o que está
vigente, responde com citação obrigatória e registra a consulta em um CRM. Registrar no CRM é
uma escrita: passa por aprovação humana antes de executar.

**Nível de autonomia: 2 (co-piloto com autorização).** O agente lê e propõe; toda mutação
externa exige confirmação. Ele não interpreta a norma, não dá parecer jurídico e não afirma
conformidade — ele localiza, cita e mostra a origem.

**Por que este domínio.** A escolha não é estética, é técnica. Quatro propriedades dele
justificam a arquitetura inteira:

1. Uma pergunta por número de norma (*"o que diz a Resolução 4.658?"*) só é atendida por
   busca por termo exato — embedding não distingue `4.658` de `4.568`. Uma pergunta por
   conceito (*"requisitos para contratação de computação em nuvem"*) só é atendida por busca
   vetorial. O domínio **força** a busca híbrida: o RRF existe por necessidade, não para
   preencher currículo.
2. Toda pergunta tem gabarito objetivo (norma e artigo corretos), então a recuperação é
   medida em número: `recall@k` e `MRR`. Não há "achei que ficou melhor".
3. Alucinação é detectável deterministicamente: se a resposta cita um artigo que não está nos
   trechos recuperados, o código rejeita. É guardrail, não LLM julgando LLM.
4. O corpus é público, gratuito, em português e denso. Sem custo, sem LGPD, sem dado
   sintético fingindo ser real.

---

## 2. Escopo — o que NÃO fazer

Não implementar, mesmo que pareça agregar. Cada exclusão é argumento de entrevista:

| Excluído | Por quê |
|---|---|
| **CrewAI** | Multi-agente conversacional resolve coordenação entre papéis. Este problema é um fluxo determinístico de 8 nós — um grafo explícito é mais confiável e mais barato |
| **LlamaIndex** | Sobreposição quase total com o que o LangGraph + retrievers próprios já fazem aqui. Duas abstrações de recuperação no mesmo projeto é dívida, não portfólio |
| **LangChain agents / chains** | O `AgentExecutor` é um loop ReAct aberto. O projeto existe justamente para demonstrar máquina de estados com limite rígido de passos. Usar `langchain-core` só para tipos de `Document` e retriever |
| **Pinecone / Weaviate** | O adapter de vetorial já prova que a troca é uma variável de ambiente. Assinar dois SaaS a mais não acrescenta arquitetura, só custo |
| **Fine-tuning** | O problema é recuperação, não estilo. Fine-tuning aqui pioraria a rastreabilidade da citação |
| **Frontend / React** | O Swagger UI do FastAPI é suficiente para demonstrar a API. Tempo em CSS não é tempo em engenharia de IA |
| **AKS / Kubernetes** | Container Apps entrega scale-to-zero e escala horizontal sem operar cluster. Escolher a ferramenta menor é a decisão sênior |
| **Multi-tenant / OAuth** | Uma API key por header cobre o requisito. Autenticação real seria escopo de outro projeto |
| **Modelo local acima de 3B** | Um 7B em 8 GB de RAM inviabiliza o resto do stack. O `qwen2.5:3b` existe para provar portabilidade, não para carregar o produto |
| **Interpretação jurídica** | O agente cita e mostra a origem. Opinar sobre conformidade é risco real, não feature |
| **MCP server** | Nada aqui precisa ser exposto a outro host de IA |
| **Cliente HTTP para plataforma de governança** | O contrato da GABBI não é público. Fingir integração real é detectável em 30 segundos e o efeito vira negativo. A Fase 9 exporta um payload no formato inferido e diz que é inferido |
| **RBAC / multiusuário na governança** | Papéis e permissões são responsabilidade da plataforma que consome o inventário, não de quem o publica |
| **Model registry / detecção de drift** | O corpus é normativo e estático e o modelo não é treinado aqui. Drift sem retreino é métrica sem sujeito |
| **Quarta tool de governança** | O agente não se audita. A ficha é endpoint e artefato de CI, nunca algo que o LLM consulta ou escreve — separar quem age de quem presta contas é o ponto |

Escrever essa tabela no `README.md` final do repositório. **É a seção que mais diferencia o
projeto.**

### 2.1 A exceção do painel — por que ela não contradiz a linha do frontend

A Fase 9 gera um `painel.html`. Isso **não** reabre a exclusão de frontend acima, e a
distinção precisa estar escrita no README porque um avaliador atento vai cobrar:

* O que foi excluído é **interface de produto** — React, build, CSS, estado de UI. A
  justificativa era "tempo em CSS não é tempo em engenharia de IA", e ela continua de pé.
* O que a Fase 9 entrega é **artefato de evidência**: um arquivo HTML único, autocontido,
  gerado por script Python a partir de dados reais, sem React, sem build, sem CDN, sem
  framework de CSS e sem dependência nova no `pyproject.toml`. Abre com a rede desligada.

Um relatório gerado por código não é um frontend, pela mesma razão que um gráfico do
`matplotlib` não é. Se o painel exigir uma dependência de UI para existir, ele saiu do
escopo — parar e reavaliar.

---

## 3. Stack

Restrição inegociável: **tudo gratuito**. Máquina alvo: 8 GB de RAM, CPU sem GPU.

| Camada | Escolha | Por quê |
|---|---|---|
| Linguagem | Python 3.11+ | |
| Orquestração | **LangGraph** | `StateGraph` tipado, checkpointing, `interrupt()` para HITL |
| LLM primário | **Groq** (free tier) | API compatível com OpenAI. O mesmo código roda em Azure OpenAI trocando variável de ambiente. Substituiu o GitHub Models, encerrado em 30/07/2026 — ver §15, item 2 |
| LLM portabilidade | Ollama + `qwen2.5:3b` (~2 GB) | Prova o requisito "open-source via Ollama" sem estourar a RAM |
| Embeddings | `fastembed` (ONNX, CPU, ~130 MB) | Roda em CPU e reindexar não gera fatura |
| Busca esparsa | `rank_bm25` | Puro Python, sem serviço externo |
| Re-ranking | `FlashRank` (~34 MB) | É o passo que mais melhora RAG; esta versão cabe em 8 GB e responde em milissegundos |
| Vetorial | Chroma local + adapter para Azure AI Search | O adapter é o que prova arquitetura de software |
| Estado / catálogo | SQLite no dev, PostgreSQL no Azure | Mesma interface via checkpointer do LangGraph |
| API | FastAPI + `httpx` | |
| Contratos | Pydantic v2 | |
| Workflow | n8n self-hosted (Docker) | O n8n Cloud é pago; o self-hosted não |
| Observabilidade | **Langfuse Cloud** (free tier) | O self-hosted v3 exige ClickHouse + Redis + MinIO — não cabe em 8 GB |
| Testes | `pytest`, `ruff` | |
| Deploy | Azure Container Apps, scale-to-zero | Franquia mensal permanente; R$ 0 no tráfego de um portfólio |
| CI/CD | GitHub Actions | Ilimitado em repositório público |

**Nenhuma dependência além destas sem perguntar antes.**

### 3.1 Regra de máquina — obrigatória

Nunca subir o `docker-compose` inteiro durante o desenvolvimento. São quatro serviços e a
máquina tem 8 GB.

* **Dev:** processo Python + Chroma embutido + SQLite. Nada de container.
* **Demo:** sobe n8n e o CRM falso, roda o fluxo, derruba.
* Um único modelo ONNX carregado por vez. `fastembed` e `FlashRank` compartilham o
  `onnxruntime` — não instalar variantes GPU.

### 3.2 Camada de provedor de LLM

Uma interface, três implementações. É o ponto que sustenta o requisito de "OpenAI/Azure
OpenAI e modelos open-source via Ollama":

```
src/copiloto/llm/provedor.py     # Protocol: gerar(mensagens, tools) -> RespostaLLM
                ├── groq.py             # padrão, gratuito
                ├── azure_openai.py     # mesma API, muda endpoint e header
                └── ollama.py           # qwen2.5:3b, fallback e prova de portabilidade
```

Selecionado por `LLM_PROVIDER` no `.env`. Um teste roda o mesmo caso nos três e compara o
resultado — a diferença de qualidade entre eles vira uma linha do README, com número.

---

## 4. Corpus

### 4.1 Montagem — confirmar antes de codar

**Não cravar número de norma nem URL de API a partir de memória.** O caminho dos dados
abertos da Receita Federal mudou no meio de um projeto anterior; a mesma lição vale aqui.

Antes da Fase 2, executar nesta ordem e **reportar o resultado antes de prosseguir**:

1. ~~Abrir a Busca de Normas do BCB e verificar se há endpoint JSON estável.~~
   **Resolvido:** há, e as URLs exatas estão em `config/corpus.toml` `[fonte]` com
   `verificado_em`. Ver §15.1 para o contrato e a pegadinha da paginação.
2. ~~Confirmar a URL corrente do endpoint do GitHub Models.~~ **Resolvido na Fase 4:** o
   GitHub Models foi encerrado por inteiro em 30/07/2026. O provedor primário passou a ser
   a Groq (`https://api.groq.com/openai/v1`).
3. Fixar o corpus em **20 a 40 normativos** de um tema coerente: segurança cibernética,
   computação em nuvem e continuidade de negócios. Corpus pequeno e coeso produz eval
   melhor que corpus grande e disperso.
4. Listar cada norma em `config/corpus.toml` com: tipo, número, ano, URL, tema e
   **status de vigência**.

### 4.2 Armadilhas de extração — todas obrigatórias

1. **Estrutura antes de tamanho.** O chunk precisa carregar o artigo, parágrafo e inciso a
   que pertence. Se essa metadata se perde, a citação é impossível e o projeto inteiro cai.
   Quebrar por artigo, nunca por contagem cega de caracteres.
2. **Vigência é campo, não texto.** Uma norma revogada responder como vigente é o pior erro
   possível neste domínio. Vai para o catálogo SQL e vira teste.
3. **Encoding.** Verificar acentuação logo na Fase 1. Se `Ç`, `Ã` ou `É` vierem quebrados,
   parar e corrigir antes de indexar qualquer coisa.
4. **HTML e PDF misturados.** Normalizar para texto limpo com a estrutura preservada; testar
   com um documento de cada formato antes de rodar o corpus todo.
5. **Referências cruzadas.** Normas citam normas ("nos termos da Resolução X"). Não seguir o
   link automaticamente — indexar só o texto do documento, senão o corpus vira grafo infinito.
6. **Chunk pai e chunk filho.** Vetorizar o parágrafo; entregar ao LLM o artigo inteiro.
   Precisão na busca, coerência no contexto.

---

## 5. Estrutura do repositório

```
copiloto-normativo/
├── CLAUDE.md                       # convenções do projeto (ver §11)
├── README.md                       # portfólio (Fase 8)
├── pyproject.toml
├── .env.example
├── .gitignore                      # /data, /índices e .env FORA do versionamento
├── .claude/
│   ├── settings.json
│   ├── hooks.json                  # ruff no PostToolUse
│   ├── rules/
│   │   ├── recuperacao.md          # paths: ["src/copiloto/recuperacao/**"]
│   │   ├── tools.md                # paths: ["src/copiloto/tools/**"]
│   │   └── grafo.md                # paths: ["src/copiloto/grafo/**"]
│   └── skills/
│       ├── rodar-evals/SKILL.md
│       ├── deploy-azure/SKILL.md
│       └── gerar-painel-governanca/SKILL.md
├── config/
│   ├── corpus.toml                 # lista de normativos + status de vigência
│   ├── parametros.toml             # k, thresholds, max_steps, peso do RRF
│   └── governanca.yaml             # ficha do sistema de IA (Fase 9)
├── docs/
│   ├── GOVERNANCA_IA.md            # núcleo neutro: NIST AI RMF, ISO 42001, EU AI Act
│   └── GABBI_READY.md              # anexo: ponte para NEXT.AI / GABBI
├── src/copiloto/
│   ├── ingestao/
│   │   ├── coleta.py               # download com retry e backoff
│   │   ├── extracao.py             # HTML/PDF -> texto com estrutura de artigo
│   │   ├── chunking.py             # pai/filho por artigo
│   │   └── indexacao.py            # Chroma + BM25 + catálogo SQL
│   ├── recuperacao/
│   │   ├── denso.py                # fastembed + Chroma
│   │   ├── esparso.py              # BM25
│   │   ├── fusao.py                # RRF
│   │   ├── rerank.py               # FlashRank
│   │   ├── retriever.py            # orquestra o pipeline
│   │   └── adapters/
│   │       ├── base.py             # Protocol do vetorial
│   │       ├── chroma.py
│   │       └── azure_ai_search.py
│   ├── tools/
│   │   ├── schemas.py              # Pydantic, extra='forbid'
│   │   ├── buscar_normativo.py
│   │   ├── consultar_catalogo.py
│   │   └── registrar_crm.py
│   ├── grafo/
│   │   ├── estado.py               # AgentState tipado
│   │   ├── nos.py
│   │   ├── guardrails.py           # entrada, saída, validador de citação
│   │   └── grafo.py                # StateGraph + checkpointer
│   ├── llm/
│   │   ├── provedor.py
│   │   ├── groq.py
│   │   ├── azure_openai.py
│   │   └── ollama.py
│   ├── resiliencia.py              # backoff, circuit breaker, idempotência
│   ├── governanca/                 # Fase 9
│   │   ├── ficha.py                # Pydantic v2 — a ficha é contrato tipado
│   │   ├── inventario.py           # auto-descoberta da frota real
│   │   ├── coerencia.py            # declarado × real
│   │   ├── metricas.py             # evals + Langfuse -> indicadores
│   │   ├── exportador.py           # inventario.json + painel.html
│   │   └── rotas.py                # APIRouter /governanca/*
│   ├── api/
│   │   ├── main.py
│   │   └── rotas.py                # /perguntar, /aprovar, /saude
│   └── observabilidade/tracing.py  # Langfuse
├── evals/
│   ├── golden.jsonl                # 40 perguntas com norma+artigo esperados
│   ├── rodar.py
│   └── metricas.py                 # recall@k, MRR, faithfulness
├── crm_falso/main.py               # FastAPI mínimo que imita um CRM
├── n8n/workflow-triagem.json
├── infra/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── main.bicep
├── .github/workflows/
│   ├── ci.yml
│   └── deploy.yml
├── data/                           # gitignored
└── tests/
```

**Quem cria o quê.** Nenhuma fase cria arquivo fora desta árvore; nenhum arquivo desta árvore
fica sem dono:

| Caminho | Fase que cria |
|---|---|
| `CLAUDE.md`, `.claude/settings.json`, `.claude/hooks.json`, `.claude/rules/` | 0 |
| `pyproject.toml`, `.env.example`, `.gitignore` | 0 |
| `ingestao/extracao.py`, `ingestao/chunking.py` | 1 |
| `ingestao/coleta.py`, `ingestao/indexacao.py`, `resiliencia.py`, `config/corpus.toml` | 2 |
| `recuperacao/` inteiro (inclui `fusao.py`, `rerank.py`, `adapters/`), `config/parametros.toml`, `evals/golden.jsonl` | 3 |
| `tools/` inteiro, `llm/` inteiro | 4 |
| `grafo/` inteiro (`estado.py`, `nos.py`, `guardrails.py`, `grafo.py`) | 5 |
| `api/`, `crm_falso/`, `n8n/`, `infra/docker-compose.yml` | 6 |
| `observabilidade/tracing.py`, `evals/rodar.py`, `evals/metricas.py`, `.github/workflows/ci.yml`, `.claude/skills/rodar-evals/` | 7 |
| `infra/Dockerfile`, `infra/main.bicep`, `.github/workflows/deploy.yml`, `.claude/skills/deploy-azure/`, `README.md` | 8 |
| `config/governanca.yaml`, `src/copiloto/governanca/` inteiro, `docs/GOVERNANCA_IA.md`, `docs/GABBI_READY.md`, `.claude/skills/gerar-painel-governanca/` | 9 |
| `tests/` | cresce a cada fase, junto com o código |

---

---

## 7. Contratos das tools

Três tools. Não criar uma quarta sem perguntar.

| Tool | Requisito da vaga que cobre | Nota de design |
|---|---|---|
| `buscar_normativo` | RAG, embeddings, re-ranking, context window | Retorna trecho + norma + artigo + score |
| `consultar_catalogo_normas` | Banco de dados SQL | **Metadado é SQL, nunca RAG** |
| `registrar_consulta_crm` | API REST, Webhooks, CRM | Escrita; passa pelo HITL |

**Sobre a segunda tool — defender esta decisão na entrevista.** "Quais normas sobre nuvem
estão vigentes?" é uma consulta de metadado. Responder isso por similaridade vetorial é errado:
a resposta precisa ser completa e exata, e busca semântica não garante nenhuma das duas coisas.
Vai para SQL. Saber **onde não usar** RAG é o sinal mais forte de domínio de RAG.

Seguir o padrão canônico do `ENGENHARIA_DE_SISTEMAS_IA_ESPECIFICACAO.md` §2.1. `registrar_consulta_crm`
exige `idempotency_key` derivada de `thread_id + passo + argumentos` — o LLM pode reexecutar
a chamada após falha de rede, e duas chamadas iguais não podem virar dois registros.

**Continuam sendo três depois da Fase 9.** A governança é endpoint HTTP e artefato de CI,
nunca uma tool. Um agente que consulta a própria ficha pode citá-la para justificar o que
fez; um agente que a escreve audita a si mesmo. Separar quem age de quem presta contas é a
decisão, e ela vale a linha no README.

---

## 8. Guardrails

**Entrada:** detectar PII (CPF, e-mail, telefone) e neutralizar antes de indexar no trace;
detectar tentativa de sobrescrever instrução do sistema. Corpus normativo é público — a
injeção indireta aqui é baixa, mas o filtro fica, e o README explica o raciocínio.

**Saída — o diferencial do projeto:** validador determinístico que extrai toda citação
(`Resolução X, art. Y`) da resposta e confere, **por código**, que cada uma aparece nos
trechos recuperados naquela execução. Citação órfã reprova a resposta e força uma nova
tentativa. Máximo de 2 tentativas; depois disso, responde *"não encontrei base normativa
para isso"*.

Um agente que sabe dizer "não sei" é mais valioso que um que sempre responde. Escrever essa
frase no README.

**Vigência:** se a resposta cita norma com `status_vigencia = 'revogada'`, o guardrail obriga
o aviso explícito. Isso é teste, não boa intenção.

---

## 9. Avaliação

Três camadas, conforme `ENGENHARIA_DE_SISTEMAS_IA_ESPECIFICACAO.md` §4.2:

| Camada | O que mede | Roda no CI |
|---|---|---|
| 1 — Determinística | Schema válido, citação presente no contexto, orçamento de tokens, HTTP 200 das tools | Sim, a cada push |
| 2 — Recuperação | `recall@5` e `MRR` contra `golden.jsonl` | Sim |
| 3 — LLM-as-judge | Faithfulness, relevância, acurácia de escolha de tool | Manual, custa chamada |

`golden.jsonl` com 40 perguntas, cada uma com norma e artigo esperados. Incluir
deliberadamente: 5 perguntas cuja resposta correta é *"não sei"*, 3 sobre norma revogada e 3
que exigem SQL e não RAG. **São esses casos que separam um agente de um chatbot.**

---

## 10. Deploy Azure

1. Criar a conta gratuita. O cartão fica cadastrado e **não é cobrado dentro da cota** —
   Container Apps tem franquia mensal permanente e o scale-to-zero mantém o custo em R$ 0.
2. `infra/main.bicep`: Container App + Container Registry + PostgreSQL Flexible Server (free
   tier de 12 meses). Se o tier gratuito de Postgres não estiver disponível na região, cair
   para SQLite em volume persistente e **registrar essa decisão no README**.
3. `min_replicas = 0`. Sem isso o custo deixa de ser zero.
4. Secrets pelos secrets do Container App, nunca no Bicep versionado.
5. `deploy.yml` com OIDC — sem senha longa em secret do GitHub.
6. **Após o primeiro deploy, abrir o Cost Analysis do portal e confirmar R$ 0.** Print no
   README.
7. Configurar alerta de orçamento em R$ 5. O risco aqui é esquecimento, não cobrança
   automática.

---

## 11. Convenções de trabalho (economia de token)

Criar `CLAUDE.md` na raiz com estas regras, para não repeti-las a cada sessão. Menos de 200
linhas, conforme `how-to-CLAUDE.md`:

* **Nunca imprimir trecho de norma, chunk ou embedding no chat.** Usar contagem, `LIMIT 3`,
  ou só os IDs.
* **Desenvolver sempre sobre os 3 normativos da Fase 1.** Rodar o corpus completo só quando o
  pipeline estiver verde na amostra.
* **Editar por diff**, nunca reescrever arquivo inteiro.
* **Uma fase por sessão.** Ao terminar: commitar e reportar em até 5 linhas.
* Ao encontrar erro, colar só a última linha do traceback, não o output inteiro.
* Não instalar dependência nova sem perguntar.
* Não criar arquivo fora da árvore do §5 sem perguntar.
* Não subir o `docker-compose` inteiro no desenvolvimento (§3.1).
* `/data` e `.env` no `.gitignore`.
* Regra que vale só para um módulo vai para `.claude/rules/` com `paths`, não para o
  `CLAUDE.md` — assim só entra no contexto quando o arquivo é tocado.
* Rotina de várias etapas vira skill em `.claude/skills/`, não texto no `CLAUDE.md`.

---

## 12. Cobertura da vaga

Tabela obrigatória no README final. É o que faz o avaliador encontrar o que procura em 30
segundos:

| Requisito do anúncio | Onde está | Fase |
|---|---|---|
| Agentes de IA e sistemas baseados em LLM | `src/copiloto/grafo/` | 5 |
| Orquestradores de workflows | LangGraph + n8n | 5, 6 |
| Deploy, monitoramento e escala na nuvem (Azure) | `infra/main.bicep`, Container Apps, Langfuse | 7, 8 |
| Integração: APIs REST | `src/copiloto/api/` | 6 |
| Integração: Webhooks | `n8n/workflow-triagem.json` | 6 |
| Integração: CRM | `tools/registrar_crm.py` + `crm_falso/` | 4, 6 |
| Integração: bancos SQL | `tools/consultar_catalogo.py` | 2, 4 |
| Python | projeto inteiro | — |
| LangGraph | `src/copiloto/grafo/` | 5 |
| n8n | `n8n/` | 6 |
| LangChain | `langchain-core` para tipos e retriever | 3 |
| RAG: chunking inteligente | `ingestao/chunking.py` (pai/filho por artigo) | 1, 2 |
| RAG: embeddings | `recuperacao/denso.py` (fastembed) | 2 |
| RAG: re-ranking | `recuperacao/rerank.py` + tabela de ablação | 3 |
| RAG: context window | `config/parametros.toml`, orçamento de contexto | 3, 5 |
| Banco vetorial (Chroma) | `recuperacao/adapters/chroma.py` | 2 |
| Banco vetorial (Azure AI Search) | `recuperacao/adapters/azure_ai_search.py` | 3 |
| OpenAI / Azure OpenAI | `llm/groq.py`, `llm/azure_openai.py` | 4 |
| Open-source via Ollama | `llm/ollama.py` (`qwen2.5:3b`) | 4 |
| Arquitetura de software end-to-end | adapters, camada de provedor, resiliência, evals, CI/CD | todas |
| Governança de IA: ficha e classificação de risco | `config/governanca.yaml`, `docs/GOVERNANCA_IA.md` | 9 |
| Governança de IA: inventário de frota | `governanca/inventario.py`, `/governanca/inventario` | 9 |
| Governança de IA: controles verificados por código | `governanca/coerencia.py` + CI vermelho na divergência | 9 |
| Observabilidade e monitoramento de IA | Langfuse + `/governanca/metricas` + painel | 7, 9 |
| Mensuração de valor / ROI por consulta | `governanca/metricas.py`, custo real por trace | 9 |
| Frameworks: NIST AI RMF, ISO/IEC 42001, EU AI Act, LGPD | `docs/GOVERNANCA_IA.md` | 9 |
| **CrewAI, LlamaIndex, Pinecone, Weaviate** | **deliberadamente fora — §2** | — |

A última linha é intencional. Diz ao avaliador que a ausência é escolha, não desconhecimento.

As seis linhas da Fase 9 são o outro lado da tabela: as demais provam saber **construir** um
agente; estas provam saber **governar** uma frota deles. Ver §16.

---

## 13. Parâmetros fixados

Conteúdo inicial de `config/parametros.toml`:

```toml
[recuperacao]
k_denso          = 30      # candidatos da busca vetorial
k_esparso        = 30      # candidatos do BM25
k_rrf            = 60      # constante do Reciprocal Rank Fusion
k_final          = 5       # trechos entregues ao LLM após o rerank
score_minimo     = 0.5     # abaixo disso, descarta

[grafo]
max_passos       = 8       # limite rígido contra loop infinito
max_autocorrecao = 2       # tentativas de correção de schema antes do fallback

[llm]
temperatura      = 0.0     # domínio normativo não admite criatividade
max_tokens_ctx   = 8000    # orçamento de contexto

[evals]
faithfulness_min = 0.95
relevancia_min   = 0.85
erro_tool_max    = 0.02
recall_5_min     = 0.80
```

Alterar valor aqui nunca deve exigir mexer no Python.

Os thresholds de `[evals]` vêm do `ENGENHARIA_DE_SISTEMAS_IA_ESPECIFICACAO.md` §4.2 e são
fixos. Os de `[recuperacao]` são **ponto de partida**, não verdade: a tabela de ablação da
Fase 3 é que define os valores finais. Se a medição contradisser estes números, vale a
medição — e a mudança é registrada no README.

---

## 14. Passo a passo do operador (Marcos)

1. Criar a pasta `copiloto-normativo` e abrir no VS Code.
2. Copiar este arquivo para dentro dela.
3. Abrir o Claude Code e dizer: *"leia BRIEFING_COPILOTO_NORMATIVO.md e execute a Fase 0"*.
4. Uma fase por sessão. Ao fim de cada: revisar o diff, rodar `pytest`, commitar, **fechar a
   sessão**. Sessão nova para a próxima fase — é onde está a economia de token.
5. **Antes da Fase 2**, confirmar com o Claude o resultado das três verificações do §4.1
   (endpoint do BCB, URL do provedor de LLM, lista do corpus).
6. **Antes da Fase 8**, criar a conta Azure e configurar o alerta de orçamento.
7. Depois da Fase 8, confirmar o custo em R$ 0 no portal e guardar o print.
8. **Antes da Fase 9**, reler o material público da SPREAD (§15, item 4) e conferir se o
   vocabulário do NEXT.AI e da GABBI continua como está descrito no §16.7.
9. Depois da Fase 9, rodar a prova viva à mão uma vez: mudar `max_passos` no
   `parametros.toml`, ver o `pytest` ficar vermelho, reverter, ver ficar verde. É a
   demonstração que se conta em entrevista — e você precisa tê-la visto acontecer.

---

## 15. Pendências reais a resolver na execução

Quatro, e nenhuma pode ser resolvida de memória:

1. ~~**Endpoint de normas do BCB.**~~ **Resolvida na Fase 2, reverificada em 10/09/2026.**
   Há JSON estável, e o corpus não precisou ser curado à mão:
   `api/conteudo/app/normativos/exibenormativo?p1=<tipo>&p2=<número>` devolve `conteudo[]`
   com `Texto`, `Revogado` e `Data` — vigência é campo da fonte, que é o que o §4.2 exige.
   A busca (`api/search/app/normativos/buscanormativos`) serviu só à curadoria e exige
   `querytext`, `startrow` e `rowlimit`: sem os três responde 500 em HTML latin-1. As duas
   URLs vivem em `config/corpus.toml` `[fonte]` com a data da verificação, e os testes
   `rede` de `tests/test_coleta.py` batem na fonte real — mudança de contrato aparece ali,
   não num corpus meio baixado.
2. ~~**URL corrente do GitHub Models.**~~ **Resolvida na Fase 4, em 10/09/2026.** Não era
   mudança de host: a GitHub encerrou o produto inteiro em 30/07/2026 — playground, catálogo
   e API de inferência. Provedor primário passou a ser a Groq, mesma API no estilo OpenAI,
   free tier sem cartão. Trocou-se um arquivo (`llm/groq.py`); nada mais do sistema mudou,
   porque tudo fala com o `Protocol` de `llm/provedor.py`. É a justificativa da camada de
   provedor, agora com prova.
3. ~~**Free tier de PostgreSQL na região do Azure escolhida.**~~ **Resolvida na Fase 8, em
   11/09/2026 — o fallback foi acionado.** Três razões somadas: a Microsoft não publica em
   quais regiões o trial está habilitado (o gate é no portal, na criação, e não há como
   confirmar sem subscription ativa); são 12 meses, contra a franquia permanente do
   Container Apps, então a promessa de R$ 0 quebraria no mês 13; e o checkpointer é
   `SqliteSaver`, de modo que trocar exigiria `langgraph-checkpoint-postgres`, fora da lista
   fechada do §3. SQLite em Azure Files montado em `/mnt/estado` — e é essa escolha que fixa
   `maxReplicas: 1`, porque SQLite sobre SMB não tem lock confiável entre máquinas.
   **O §10.2 perdeu também o ACR**, por motivo independente: ele não tem free tier de espécie
   alguma e o Basic é cobrado por dia, o que contradiz o critério de pronto da própria fase.
   A imagem vai para o GitHub Container Registry, gratuito para pacote público.
4. ~~**Material público da SPREAD sobre NEXT.AI e GABBI.**~~ **Reverificada na Fase 9, em
   11/09/2026.** `spread.com.br/next-ai/` respondeu com um desafio anti-bot (`sgcaptcha`) na
   releitura — não contornado. A confirmação veio de buscas que indexaram o conteúdo público
   da página: os cinco pilares (Strategy & Alignment, Governance & Compliance, Operations,
   Observability & Monitoring, Value Generation) e as capacidades da GABBI (visibilidade,
   políticas e papéis, monitoramento, conformidade, ROI) continuam descritos como o §16.7 já
   previa. `docs/GABBI_READY.md` abre com o aviso de contrato inferido e registra como a
   verificação foi feita — não é leitura direta da página, e o documento diz isso.

Resolver cada uma **antes** da fase que depende dela, e reportar antes de prosseguir.

**Achado da Fase 9 que revisou o §16.1.** O desenho original previa 4 sistemas na frota —
`groq`, `azure_openai`, `ollama`, `eval-judge/camada-3`. `azure_openai.py` e `ollama.py`
nunca chegaram a existir no repositório (`api/main.py::provedor_do_ambiente` falha alto de
propósito se alguém os pedir). Decisão tomada com o operador antes de escrever
`governanca/inventario.py`: a varredura inventaria só o que o código implementa de fato —
`groq` e `eval-judge/camada-3` — e os outros dois entram na ficha como
`implementado: false`, nunca como entrada fictícia. É a mesma regra do §1.4 contra dado
sintético, aplicada à própria camada que existe para cobrar isso dos outros.

---

## 16. Fase 9 — Camada de governança de IA

### Por que ela existe

As oito primeiras fases provam saber **construir** um agente. Nenhuma prova saber **governar**
uma frota deles — e é essa a disciplina que a plataforma-alvo endereça. A camada não inventa
insumo novo: o projeto já declara nível de autonomia (§1), justifica decisões negativas (§2),
valida citação por código (§8), fixa thresholds de eval (§9) e mede custo por nó (Fase 7).
Tudo isso está espalhado por oito fases e nunca é **declarado como inventário**.

A regra que separa esta camada de um documento bonito: **a ficha é validada contra o código
real**. Se o código divergir do que a ficha declara, o CI fica vermelho. É a mesma lógica do
guardrail de citação do §8 e da tabela de ablação da Fase 3 — medida, não afirmada. Governança
que ninguém verifica é papel.

### 16.1 A frota real — o achado

O repositório já contém **quatro sistemas de IA distintos** sob a ótica de governança:

| # | Sistema | Origem | Nota |
|---|---|---|---|
| 1 | `copiloto/groq` | `llm/groq.py` | Primário |
| 2 | `copiloto/azure-openai` | `llm/azure_openai.py` | Mesma API, outro fornecedor, outro custo |
| 3 | `copiloto/ollama-qwen2.5-3b` | `llm/ollama.py` | Modelo aberto, hospedagem local, outro perfil de risco |
| 4 | `eval-judge/camada-3` | `evals/rodar.py` (§9) | O LLM que julga o outro LLM |

O item 4 é o argumento mais forte da fase inteira e vai escrito no `docs/GOVERNANCA_IA.md`:
**um LLM que avalia outro LLM é um sistema de IA em produção, e é o que mais escapa dos
inventários reais.** Ele influencia decisões de release, consome orçamento, tem fornecedor e
pode falhar — e quase nunca aparece na planilha de nenhuma empresa.

`inventario.py` monta essa lista varrendo o repositório. **Nenhuma entrada fictícia, nenhuma
fixture** — a regra do §1.4 contra dado sintético vale aqui como vale no corpus.

### 16.2 `config/governanca.yaml`

Organizado nas quatro funções do NIST AI RMF (GOVERN, MAP, MEASURE, MANAGE):

* `identificacao` — id, nome, versão, dono, contato, data de registro
* `proposito` — descrição, domínio, público-alvo e **casos de uso vedados**, que já vêm
  prontos das §1 e §2: não interpreta norma, não dá parecer jurídico, não afirma conformidade
* `classificacao_risco` — nível segundo o EU AI Act, justificativa escrita, framework de
  referência. *O valor está em fazer a análise e defendê-la, não na resposta que ela dá*
* `autonomia` — nível 2, ações autônomas, ações que exigem aprovação (`registrar_consulta_crm`)
* `modelos[]` — provedor, modelo, fornecedor, hospedagem, papel (primário/fallback/juiz)
* `dados` — corpus público do BCB, ausência de PII indexada, base legal LGPD, retenção
* `limites_operacionais` — espelham `parametros.toml`; validados por `coerencia.py`
* `controles[]` — id, tipo (entrada/saída), determinístico sim ou não, e o **caminho do teste
  que prova o controle**
* `metricas` — os thresholds do §13 e onde cada um é medido
* `incidentes` — o fallback ("não encontrei base normativa para isso")
* `ciclo_de_vida` — status, data da última avaliação, gatilhos de reavaliação

Pydantic v2 com `extra='forbid'` em `ficha.py` — a mesma disciplina de contrato do §7, nenhuma
convenção nova. A ficha é um contrato tipado, não um documento.

### 16.3 `coerencia.py` — o coração técnico

| Verificação | Declarado | Real |
|---|---|---|
| Limites operacionais | `governanca.yaml` | `[grafo]` e `[llm]` do `parametros.toml` |
| Thresholds de eval | `governanca.yaml` | `[evals]` do `parametros.toml` |
| Modelos inventariados | `governanca.yaml` | provedores registrados no Protocol do §3.2 |
| Controles com prova | `controles[].teste` | o teste existe e passa |

`parametros.toml` **é**; a ficha **declara**. Onde os dois discordam, quem está errado é a
ficha. `GET /governanca/coerencia` responde 200 com lista vazia ou 409 com as divergências,
e `tests/test_coerencia.py` reprova o CI em qualquer uma.

### 16.4 Endpoints

Um `APIRouter` montado no `api/main.py` da Fase 6:

* `GET /governanca/ficha` — a ficha validada, em JSON
* `GET /governanca/inventario` — a frota do §16.1
* `GET /governanca/metricas` — faithfulness, `recall@5`, custo por consulta, latência p95,
  taxa de citação órfã, razão aprovado/rejeitado no HITL
* `GET /governanca/coerencia` — 200 ou 409

### 16.5 Painel

`exportador.py` gera dois arquivos: `inventario.json`, no formato inferido de plataforma de
governança, e `painel.html`. O painel é arquivo único e autocontido, montado com
`string.Template` a partir dos dados reais. Sem React, sem build, sem CDN, sem framework de
CSS e **sem dependência nova** — ver §2.1. Abre com a rede desligada.

Mostra a frota, as métricas, o estado de coerência e a linha do tempo de versões da ficha.

### 16.6 ROI, com honestidade

`metricas.py` calcula o custo real por consulta a partir dos traces do Langfuse — esse número
é medição. O ganho comparado ao tempo de busca manual em norma é **premissa declarada, não
medição**, e aparece rotulado assim no painel e no README.

Premissa marcada como premissa é engenharia. Premissa disfarçada de número contradiz tudo o
que as Fases 3 e 7 defendem.

### 16.7 Os dois documentos

* `docs/GOVERNANCA_IA.md` — núcleo neutro, ancorado em NIST AI RMF, ISO/IEC 42001, EU AI Act
  e LGPD. Vocabulário público, citável e que sobrevive a qualquer leitor.
* `docs/GABBI_READY.md` — anexo curto que faz a ponte: mapeia cada artefato do projeto para
  os cinco pilares do NEXT.AI (*Strategy & Alignment, Governance & Compliance, Operations,
  Observability & Monitoring, Value Generation*) e para as capacidades públicas da GABBI —
  inventário consolidado, políticas e papéis, observabilidade, mensuração de ROI.

  **Abre com aviso explícito:** o contrato de integração foi inferido do material público da
  Spread; não há API oficial documentada e nenhuma integração real é feita. O aviso não
  enfraquece o documento — é o que o torna confiável.
