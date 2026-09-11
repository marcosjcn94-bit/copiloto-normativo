# Copiloto Normativo BCB

Responde perguntas sobre normas do Banco Central citando **norma, artigo e status de
vigência** — e a citação é conferida por código antes de chegar ao usuário. Se o validador
não achar o artigo citado dentro do trecho recuperado, a resposta não sai.

Não interpreta a norma, não dá parecer jurídico e não afirma conformidade. Isso é escopo,
não limitação: quem opina sobre conformidade assume risco que um copiloto de recuperação não
tem como cobrir.

**Corpus:** 22 normativos do BCB sobre segurança cibernética, computação em nuvem,
continuidade e risco operacional — 344 artigos, incluindo **6 normas revogadas de
propósito**, porque saber que a norma caiu é metade da resposta.

```
┌──────────┐  webhook   ┌─────────┐   REST   ┌───────────────────────────────┐
│   n8n    │───────────▶│ FastAPI │─────────▶│ LangGraph — 6 nós, teto rígido│
└──────────┘            └─────────┘          └───────────────┬───────────────┘
                                                             │
                       ┌─────────────────────────────────────┼──────────────────┐
                       ▼                    ▼                ▼                  ▼
                buscar_normativo    consultar_catalogo  registrar_crm       guardrails
                 (RAG híbrido)        (SQL, não RAG)   (HITL obrigatório) (valida citação)
```

## Prove em dois minutos

> **Estado do deploy:** a infraestrutura está escrita, o Bicep compila limpo e a imagem foi
> construída e testada localmente — o container sobe, assa as 22 normas no build e devolve
> `/saude` 200 no primeiro cold start. O endpoint público sobe no primeiro `git tag v*`.
> Trocar `<host>` pela saída do deploy.

```bash
curl https://<host>/saude

curl -X POST https://<host>/perguntar -H 'Content-Type: application/json' \
  -d '{"pergunta":"Preciso manter política de segurança cibernética?"}'
```

Três perguntas que separam este projeto de um chatbot:

| Pergunta | O que deve acontecer |
|---|---|
| "A Resolução 4.658 continua valendo?" | Responde que **foi revogada**, e diz por qual norma |
| "Quais normas sobre nuvem estão vigentes?" | Vai para **SQL**, não para busca vetorial — metadado exige resposta exata e completa |
| "Qual o prazo do artigo 9º da Circular 3.999?" | Responde **"não encontrei base normativa"**. A norma não existe no corpus, e inventar é o modo de falha caro |

`/docs` serve o Swagger. Não há frontend, e isso é decisão — ver a tabela de escopo.

## Os números

Recuperação, medida sobre `evals/golden.jsonl` — 40 perguntas, das quais as **32 do tipo
`rag`** têm norma e artigo esperados (as outras 8 são os casos de "não sei", norma revogada e
consulta que exige SQL):

| Configuração | recall@5 | MRR |
|---|---|---|
| Só vetorial | 0.625 | 0.511 |
| Híbrido (RRF) | 0.781 | 0.569 |
| Híbrido + re-ranking | 0.844 | 0.673 |
| Híbrido + re-ranking + escopo por norma | **0.844** | **0.694** |

Cada linha é uma medição, e a tabela é o que autoriza a complexidade: o re-ranking só está no
pipeline porque move 0,063 de recall e 0,104 de MRR. O escopo por norma **não move o
recall** — move o MRR, porque acerta mais no topo. Quem só olhasse recall teria concluído que
era inútil.

E a varredura que desmentiu um palpite: `k = 30` foi comparado com 60, 100 e 150. `k = 150`
ganha **uma pergunta das 32** a 4,7× da latência, e a curva não é monotônica — 60 é pior que
30 e que 100. Curva que sobe e desce assim é variância de quem ordena, não propriedade da
largura. Ficou em 30, e `evals/ablacao.py --varredura` refaz a conta.

Qualidade da resposta, três camadas (`evals/rodar.py --todas`):

| Métrica | Medido | Exigido |
|---|---|---|
| `recall_5` | 0.844 | ≥ 0.80 |
| `faithfulness_citacao` (validador determinístico) | 1.00 | ≥ 0.95 |
| `faithfulness_juiz` (LLM como juiz) | 1.00 | ≥ 0.95 |
| `relevancia_juiz` | 1.00 | ≥ 0.85 |
| `taxa_erro_tool` | 0.00 | ≤ 0.02 |

A camada 1 é determinística e roda no CI sem corpus: contrato das tools, correspondência
entre os nomes de tool do prompt e os do registro, e orçamento de contexto. Ela **já pegou um
bug real** — `c1.orcamento_real` ficou vermelha com 14.932 tokens no pior caso. A causa não
era o `k_final`; era **um artigo de 16 mil caracteres**. O teto por artigo derrubou o pior
caso para 3.562 tokens *entregando 8 artigos em vez de 5*: mais contexto útil e menos
contexto. Artigo cortado avisa que foi cortado, dentro do próprio texto — truncar em silêncio
faz o modelo concluir pela ausência do inciso que sumiu.

## O que NÃO foi implementado

Cada exclusão é uma decisão, e a lista importa tanto quanto o que foi construído.

| Excluído | Por quê |
|---|---|
| **CrewAI** | Multi-agente conversacional resolve coordenação entre papéis. Isto é um fluxo determinístico de 6 nós (`sanear → deliberar → verificar → aprovar/recusar → escrever`) — grafo explícito é mais confiável e mais barato |
| **LlamaIndex** | Sobreposição quase total com LangGraph + retrievers próprios. Duas abstrações de recuperação no mesmo projeto é dívida, não portfólio |
| **LangChain agents / chains** | `AgentExecutor` é um loop ReAct aberto. O projeto existe para demonstrar máquina de estados com teto rígido de passos |
| **Pinecone / Weaviate** | O adapter de vetorial já prova que a troca é variável de ambiente. Dois SaaS a mais não acrescentam arquitetura, só custo |
| **Fine-tuning** | O problema é recuperação, não estilo. Aqui pioraria a rastreabilidade da citação |
| **Frontend / React** | O Swagger do FastAPI demonstra a API. Tempo em CSS não é tempo em engenharia de IA |
| **AKS / Kubernetes** | Container Apps entrega scale-to-zero e escala horizontal sem operar cluster. Escolher a ferramenta menor é a decisão sênior |
| **Multi-tenant / OAuth** | Uma API key por header cobre o requisito. Autenticação real seria outro projeto |
| **Modelo local acima de 3B** | Um 7B em 8 GB inviabiliza o resto do stack. O `qwen2.5:3b` existe para provar portabilidade, não para carregar o produto |
| **Interpretação jurídica** | O agente cita e mostra a origem. Opinar sobre conformidade é risco real, não feature |
| **MCP server** | Nada aqui precisa ser exposto a outro host de IA |
| **Cliente HTTP para plataforma de governança** | O contrato não é público. Fingir integração real é detectável em 30 segundos |
| **RBAC na governança** | Papéis são responsabilidade de quem consome o inventário, não de quem o publica |
| **Model registry / detecção de drift** | Corpus normativo e estático, modelo não treinado aqui. Drift sem retreino é métrica sem sujeito |
| **Quarta tool de governança** | O agente não se audita. A ficha é endpoint e artefato de CI, nunca algo que o LLM consulta |

## Três decisões que contrariaram o plano original

O plano deste projeto foi escrito antes do código. Três pontos dele não sobreviveram ao
contato com a realidade — e o registro das trocas vale mais que a aparência de um plano que
deu certo de primeira.

### 1. PostgreSQL virou SQLite em volume

O plano previa PostgreSQL Flexible Server no free tier de 12 meses. Foi **verificado antes do
deploy**, não assumido, e o resultado derrubou a escolha por três razões somadas:

* A Microsoft **não publica** em quais regiões esse trial está habilitado — o gate acontece no
  portal, no momento da criação, e não há como confirmar sem uma subscription ativa.
* São **12 meses**. A franquia do Container Apps (180.000 vCPU-s e 360.000 GiB-s por mês) é
  permanente. Um portfólio com prazo de validade no mês 13 não atende ao critério "R$ 0".
* O checkpointer de hoje é `SqliteSaver`. Trocar exigiria `langgraph-checkpoint-postgres` —
  dependência fora da lista fechada do projeto, para resolver um problema que ainda não existe.

O estado vai para SQLite num Azure Files montado em `/mnt/estado`. **E é essa escolha que fixa
`maxReplicas: 1`** — SQLite sobre SMB não tem lock confiável entre máquinas, e duas réplicas
gravando no mesmo arquivo corrompem justamente o checkpoint que sustenta o HITL. Escalar
horizontalmente exige trocar o checkpointer primeiro. É aí, e só aí, que o PostgreSQL volta à
mesa.

### 2. Azure Container Registry virou GitHub Container Registry

O ACR **não tem free tier** — o Basic é o mais barato e é cobrado por dia. Um registry pago
para hospedar a imagem de um portfólio contradiz o critério de pronto da própria fase de
deploy. A imagem vai para o GHCR, gratuito para pacote público, e o Container Apps a puxa
anonimamente. O Bicep mantém `registroUsuario` / `registroSenha` para o caso de o pacote
precisar ficar privado.

### 3. O provedor de LLM mudou no meio do caminho

O plano usava GitHub Models. A GitHub encerrou o produto inteiro em 30/07/2026 — playground,
catálogo e API de inferência. O provedor primário passou a ser a Groq. **Mudou um arquivo**
(`llm/groq.py`), e nada mais do sistema se mexeu, porque tudo fala com o `Protocol` de
`llm/provedor.py`.

Era a justificativa da camada de provedor. Agora é a prova dela.

## Escrita externa passa por gente

O agente **propõe** o registro no CRM; não o grava. O grafo para num `interrupt()` do
LangGraph, o estado fica no checkpointer, e a gravação só acontece depois de `POST /aprovar`.

Isso não é um `if` no caminho feliz: o processo pode morrer entre a proposta e a aprovação —
com `minReplicas: 0` ele **vai** morrer — e retomar de onde parou é exatamente o que o
checkpointer faz. A tool de escrita exige `idempotency_key` derivada de `thread_id + passo +
argumentos`, porque o LLM pode reexecutar a chamada depois de uma falha de rede e duas
chamadas iguais não podem virar dois registros.

## Cobertura

| Requisito | Onde está |
|---|---|
| Agentes de IA e sistemas baseados em LLM | `src/copiloto/grafo/` |
| Orquestradores de workflows | LangGraph + n8n |
| Deploy, monitoramento e escala na nuvem (Azure) | `infra/main.bicep`, Container Apps, Langfuse |
| Integração: APIs REST | `src/copiloto/api/` |
| Integração: Webhooks | `n8n/workflow-triagem.json` |
| Integração: CRM | `tools/registrar_crm.py` + `crm_falso/` |
| Integração: bancos SQL | `tools/consultar_catalogo.py` |
| Python | projeto inteiro |
| LangGraph | `src/copiloto/grafo/` |
| n8n | `n8n/` |
| LangChain | `langchain-core` para tipos e retriever |
| RAG: chunking inteligente | `ingestao/chunking.py` (pai/filho por artigo) |
| RAG: embeddings | `recuperacao/denso.py` (fastembed, ONNX/CPU) |
| RAG: re-ranking | `recuperacao/rerank.py` + tabela de ablação |
| RAG: context window | `config/parametros.toml`, orçamento de contexto medido |
| Banco vetorial (Chroma) | `recuperacao/adapters/chroma.py` |
| Banco vetorial (Azure AI Search) | `recuperacao/adapters/azure_ai_search.py` |
| OpenAI / Azure OpenAI | `llm/groq.py` — API no estilo OpenAI, atrás do `Protocol` de `llm/provedor.py` |
| Open-source via Ollama (`qwen2.5:3b`) | previsto; hoje o `Protocol` tem uma implementação só |
| Arquitetura de software end-to-end | adapters, camada de provedor, resiliência, evals, CI/CD |
| Governança de IA: ficha e classificação de risco | `config/governanca.yaml`, `docs/GOVERNANCA_IA.md` |
| Governança de IA: inventário de frota | `governanca/inventario.py` |
| Governança de IA: controles verificados por código | `governanca/coerencia.py` + CI vermelho na divergência |
| Observabilidade e monitoramento de IA | Langfuse + `/governanca/metricas` |
| Mensuração de valor / ROI por consulta | `governanca/metricas.py` |
| NIST AI RMF, ISO/IEC 42001, EU AI Act, LGPD | `docs/GOVERNANCA_IA.md` |
| **CrewAI, LlamaIndex, Pinecone, Weaviate** | **deliberadamente fora — ver a tabela de escopo** |

As linhas de governança apontam para a camada seguinte, ainda em construção; a de
observabilidade já existe pela metade — o Langfuse está de pé desde a fase de evals, o
`/governanca/metricas` não. Fora isso, o Ollama é a única linha do plano original que
continua sem implementação, e o ponto que ela ilustraria — o `Protocol` de provedor — já foi
provado de outro jeito, quando a Groq substituiu o GitHub Models sem que nada fora de `llm/`
mudasse.

## Custo

| Item | Custo |
|---|---|
| Container Apps, `minReplicas: 0` | R$ 0 — franquia mensal permanente, e um portfólio não chega a 50 h de réplica acordada |
| GHCR, pacote público | R$ 0 |
| Azure Files, share de 1 GiB | centavos/mês — a única linha não-zero do desenho |
| Log Analytics | desligado por padrão; ingestão é cobrada por GB |
| Groq, free tier | R$ 0 |
| Langfuse Cloud, free tier | R$ 0 |

> **Pendente:** o print do Cost Analysis do portal, que é o que fecha a fase de deploy. O Cost
> Analysis tem de 8 a 24 h de atraso de telemetria, então ele só prova alguma coisa no dia
> seguinte ao primeiro deploy. Roteiro em `.claude/skills/deploy-azure/SKILL.md`.

**Autenticação:** o ingress é público e `/perguntar` ainda não exige chave — a API key por
header está no escopo do projeto e não foi implementada. Até lá, o parâmetro `ipsAutorizados`
do Bicep restringe o acesso por CIDR.

## Rodar localmente

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env          # preencher GROQ_API_KEY

.venv/Scripts/python.exe -m copiloto.ingestao.indexacao   # baixa e indexa o corpus
.venv/Scripts/python.exe -m uvicorn copiloto.api.main:criar_app --factory --port 8000
```

```bash
.venv/Scripts/python.exe -m pytest                    # suíte
.venv/Scripts/python.exe -m ruff check .              # lint
.venv/Scripts/python.exe evals/rodar.py --camada 1    # o que o CI roda, sem corpus
.venv/Scripts/python.exe evals/rodar.py --todas       # + recuperação e juiz
.venv/Scripts/python.exe evals/ablacao.py             # regera evals/ablacao.md
```

Não subir o `docker-compose` inteiro em desenvolvimento: a máquina de referência tem 8 GB e o
copiloto carrega dois modelos ONNX no próprio processo. O compose sobe só o n8n e o CRM
falso, para a demonstração do webhook.

## Stack

Python 3.11–3.13 · LangGraph · Groq · Ollama (`qwen2.5:3b`) · `fastembed` (ONNX/CPU) ·
`rank_bm25` · FlashRank · Chroma (+ adapter Azure AI Search) · SQLite · FastAPI · Pydantic v2
· n8n · Langfuse · pytest · ruff · Azure Container Apps · GitHub Actions.

Restrição inegociável do projeto: **tudo gratuito, CPU, 8 GB de RAM.** Quase toda decisão
acima é consequência dela.
