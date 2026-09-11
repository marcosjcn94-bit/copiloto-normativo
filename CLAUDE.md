# Copiloto Normativo BCB — convenções do projeto

Fonte de verdade das decisões: `BRIEFING_COPILOTO_NORMATIVO.md`. Este arquivo é o resumo
operacional; em caso de conflito, vale o briefing.

## O que é

Copiloto que responde perguntas sobre normas do BCB citando **norma, artigo e status de
vigência**, com a citação verificada por código antes de chegar ao usuário. Autonomia nível 2:
lê e propõe; toda escrita externa (CRM) passa por aprovação humana.

Não interpreta a norma, não dá parecer jurídico, não afirma conformidade.

## Economia de contexto — regras duras

* **Nunca imprimir trecho de norma, chunk ou embedding no chat.** Usar contagem, `LIMIT 3` ou
  só os IDs.
* **Desenvolver sempre sobre os 3 normativos da Fase 1.** Corpus completo só com o pipeline
  verde na amostra.
* **Editar por diff**, nunca reescrever arquivo inteiro.
* Ao encontrar erro, colar **só a última linha** do traceback.
* **Uma fase por sessão.** Ao terminar: commitar e reportar em até 5 linhas.

## Regras de projeto

* Não instalar dependência nova sem perguntar. A lista fechada está no §3 do briefing.
* Não criar arquivo fora da árvore do §5 do briefing sem perguntar.
* Não subir o `docker-compose` inteiro no desenvolvimento. Dev = processo Python + Chroma
  embutido + SQLite, sem container. A máquina tem 8 GB.
* Um único modelo ONNX carregado por vez. `fastembed` e `FlashRank` compartilham o
  `onnxruntime` — nunca instalar variantes GPU.
* `/data`, `/indices` e `.env` ficam no `.gitignore`.
* Parâmetro de comportamento vive em `config/parametros.toml`. Mudar valor lá nunca deve
  exigir mexer no Python.
* Regra que vale só para um módulo vai para `.claude/rules/` com `paths`, não para este
  arquivo.
* Rotina de várias etapas vira skill em `.claude/skills/`, não texto aqui.

## Stack (fechada)

Python 3.11–3.13 · LangGraph · Groq (LLM primário) · Ollama `qwen2.5:3b`
(portabilidade) · `fastembed` (ONNX/CPU) · `rank_bm25` · FlashRank · Chroma local
(+ adapter Azure AI Search) · SQLite no dev / PostgreSQL no Azure · FastAPI · `httpx` ·
Pydantic v2 · n8n self-hosted · Langfuse Cloud · pytest · ruff · Azure Container Apps ·
GitHub Actions.

Restrição inegociável: tudo gratuito, CPU, 8 GB de RAM.

## Comandos

```bash
.venv/Scripts/python.exe -m pytest      # testes
.venv/Scripts/python.exe -m ruff check .    # lint
.venv/Scripts/python.exe -m ruff format .   # formatação

.venv/Scripts/python.exe evals/rodar.py --camada 1   # o que o CI roda, sem corpus
.venv/Scripts/python.exe evals/rodar.py --todas      # + recuperação e juiz (custa chamada)
.venv/Scripts/python.exe evals/ablacao.py            # regera evals/ablacao.md
.venv/Scripts/python.exe evals/ablacao.py --varredura # k x score_minimo (~35 min)
```

Trace do Langfuse é opcional e exige o extra: `pip install -e ".[obs]"`. Sem ele, ou sem
`LANGFUSE_*` no ambiente, o copiloto responde igual e não emite trace — `GET /saude` diz
qual dos dois falta.

## Estilo de código

* Nomes de domínio em português (`buscar_normativo`, `consultar_catalogo`); termos técnicos
  consagrados ficam em inglês (`chunk`, `rerank`, `checkpointer`).
* Contratos de tool em Pydantic v2 com `extra='forbid'`.
* Interfaces por `Protocol` (provedor de LLM, adapter vetorial), nunca herança concreta.
* Sem `print` em código de produção — logging estruturado.
* `line-length = 100`, ruff com `E,F,I,UP,B,SIM`.

## Fases

Executar em ordem, sem avançar sem o critério de pronto verde (`fases_de_execuçao.md`).

0 setup · 1 fatia fina · 2 corpus + catálogo SQL · 3 recuperação híbrida · 4 tools ·
5 grafo/HITL/guardrails · 6 API+webhook+CRM falso · 7 observabilidade/evals/CI ·
8 deploy Azure + README · 9 governança de IA.

## Pendências a resolver antes da fase que depende delas

1. ~~Endpoint de normas do BCB.~~ Resolvida na Fase 2 e reverificada em 10/09/2026: **há
   JSON estável**. `exibenormativo` (`p1` = tipo, `p2` = número) devolve `conteudo[]` com
   `Texto`, `Revogado` e `Data`. `buscanormativos` exige `querytext`, `startrow` e
   `rowlimit`. As URLs vivem em `config/corpus.toml` `[fonte]`, nunca no Python, e o
   contrato está fixado nos testes `rede` de `tests/test_coleta.py`.
2. ~~URL corrente do GitHub Models.~~ Resolvida na Fase 4: produto encerrado em 30/07/2026;
   provedor primário passou a ser a Groq (`llm/groq.py`).
3. ~~Free tier de PostgreSQL na região Azure escolhida.~~ Resolvida na Fase 8 em
   11/09/2026: a Microsoft **não publica** as regiões do trial (o gate é no portal, na
   criação) e o benefício dura 12 meses, contra a franquia permanente do Container Apps.
   Acionado o fallback do §10.2: SQLite em Azure Files, `maxReplicas: 1` por causa do
   lock sobre SMB. Junto caiu o ACR, que não tem free tier — a imagem vai para o GHCR.
   As duas trocas estão no README e em `infra/main.bicep`.
4. ~~Material público da SPREAD sobre NEXT.AI e GABBI.~~ **Reverificada na Fase 9, em
   11/09/2026.** `spread.com.br/next-ai/` ficou atrás de um desafio anti-bot (não
   contornado); a confirmação veio de buscas que indexaram o conteúdo público da página, que
   ainda descrevem os cinco pilares e as capacidades da GABBI como o §16.7 já previa. O
   aviso de contrato inferido está em `docs/GABBI_READY.md`, junto do detalhe de como a
   verificação foi feita.

Nenhuma pode ser resolvida de memória. Verificar e reportar antes de prosseguir.

## Achado da Fase 9 que muda o desenho original

O §16.1 do briefing previa 4 sistemas na frota (`groq`, `azure_openai`, `ollama`,
`eval-judge/camada-3`). `azure_openai.py` e `ollama.py` nunca existiram no repositório —
`api/main.py::provedor_do_ambiente` falha alto de propósito se alguém os pedir. Decisão
tomada com o operador: `governanca/inventario.py` varre e inventaria só o real (`groq` +
`eval-judge/camada-3`); os outros dois entram na ficha como `implementado: false`, nunca
como entrada fictícia. `pyyaml` foi aprovado como única exceção à lista fechada do §3, para
`governanca/ficha.py` carregar `config/governanca.yaml` inteiro num modelo Pydantic.
