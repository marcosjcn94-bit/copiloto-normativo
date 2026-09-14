## Copiloto Normativo BCB

Responde perguntas sobre normas do Banco Central citando **norma, artigo e status de
vigência** — e a citação é conferida por código antes de chegar ao usuário. Se o validador
não achar o artigo citado dentro do trecho recuperado, a resposta não sai.

Não interpreta a norma, não dá parecer jurídico e não afirma conformidade. Isso é escopo,
não limitação: quem opina sobre conformidade assume risco que um copiloto de recuperação não
tem como cobrir.

* Problema: Eliminar alucinações e riscos de conformidade em consultas ao arcabouço regulatório do Banco Central do Brasil (segurança cibernética, nuvem e risco operacional). 
* Solução: Desenvolvi uma arquitetura agêntica com LangGraph, integrando RAG Híbrido avançado (busca vetorial densa com FastEmbed ONNX + BM25 via Reciprocal Rank Fusion) e re-ranking neural (FlashRank). Estruturei roteamento para catálogo SQL para metadados exatos de vigência e fluxo Human-in-the-Loop (HITL) com checkpointing idempotente para escritas externas em CRM, orquestrado via FastAPI e webhooks n8n. Implementei framework de Governança de IA (alinhado a NIST AI RMF, ISO 42001 e LGPD), com inventário dinâmico de frota (incluindo LLM-as-a-Judge) e testes de coerência contínua.
*	Impacto: Atingi 84,4% de Recall, 0.694 de MRR e 100% de Faithfulness comprovados por esteira de Evals. Toda a infraestrutura foi modelada como código (IaC via Azure Bicep) e conteinerizada no Azure Container Apps, garantindo um sistema auditável e seguro contra falsos positivos.
* Stack: LangGraph, FastAPI, Groq (Llama 3), FastEmbed (ONNX/CPU), Rank-BM25, FlashRank, ChromaDB, SQLite, n8n, Langfuse, Azure Container Apps, Azure Bicep, Docker, GitHub Actions, Pydantic v2.

**Corpus:** 22 normativos do BCB sobre segurança cibernética, computação em nuvem,
continuidade e risco operacional — 344 artigos, incluindo **6 normas revogadas de
propósito**, porque saber que a norma caiu é metade da resposta.

## FinOps: Custo R$ 0

* Azure Container Apps com minReplicas: 0 (scale-to-zero) / GitHub Container Registry (GHCR) / SQLite em Azure Files / Groq (free tier) como LLM primário / qwen2.5:3b via Ollama / fastembed e FlashRank (ONNX local) / Busca esparsa rank_bm25 (puro Python) / Chroma local / n8n self-hosted (Docker) / Langfuse Cloud (free tier) / Swagger UI


## Rodar localmente

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env          # preencher GROQ_API_KEY

.venv/Scripts/python.exe -m copiloto.ingestao.indexacao   # baixa e indexa o corpus
.venv/Scripts/python.exe -m uvicorn copiloto.api.main:criar_app --factory --port 8000
```
