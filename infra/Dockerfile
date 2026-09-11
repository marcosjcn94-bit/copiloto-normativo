# syntax=docker/dockerfile:1.7
#
# Imagem do copiloto para o Azure Container Apps (Fase 8).
#
# O contexto de build é a RAIZ do repositório, não `infra/`:
#
#     docker build -f infra/Dockerfile -t copiloto-normativo .
#
# Três decisões explicam o formato desta imagem:
#
# 1. **O corpus é assado aqui, não montado.** `/data` e `/indices` estão no
#    `.gitignore` e nenhum runner os tem. Reconstruí-los durante o `docker build`
#    é o que torna a imagem autocontida — e com `min_replicas = 0` isso deixa de
#    ser preferência: o processo morre e renasce o tempo todo, e um cold start
#    que baixasse 22 normas do site do BCB seria um cold start que depende do
#    site do BCB estar no ar.
#
# 2. **Os dois modelos ONNX vêm assados junto, pelo mesmo motivo.**
#    `FASTEMBED_CACHE_PATH` e o `cache_dir` do FlashRank apontam para dentro de
#    `/app/indices`, que é exatamente onde `recuperacao/retriever.py` procura em
#    produção. Cold start que baixa 130 MB de peso não é cold start, é download.
#
# 3. **Install editável, de propósito.** `api/main.py` deriva a raiz do projeto de
#    `Path(__file__).resolve().parents[3]`. Com o pacote em `site-packages` isso
#    apontaria para dentro da instalação do Python; com `pip install -e .` em
#    `/app` aponta para `/app`, que é onde estão `config/`, `data/` e `indices/`.
#    A alternativa seria acrescentar uma variável de ambiente nova em código da
#    Fase 6 — mexer em fase fechada para acomodar empacotamento é a troca errada.

ARG PYTHON_VERSION=3.13

# --- base comum a todos os estágios ------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    FASTEMBED_CACHE_PATH=/app/indices/fastembed
WORKDIR /app

# --- 1. dependências ---------------------------------------------------------
# Camada separada e copiada antes do resto para que mexer em `config/` não
# reinstale o onnxruntime.
FROM base AS dependencias
RUN python -m venv /opt/venv
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e .

# --- 2. corpus e modelos -----------------------------------------------------
# Este estágio fala com a rede: baixa as 22 normas de `config/corpus.toml`,
# escreve `data/catalogo.sqlite`, `indices/chroma` e `indices/bm25.json`, e puxa
# os dois pesos ONNX. É o estágio caro e o que quebra quando o BCB está fora do
# ar — por isso é um estágio, e não um passo do runtime.
FROM dependencias AS corpus
COPY config ./config
RUN python -m copiloto.ingestao.indexacao

# O peso do rerank não é baixado pela indexação (quem reordena é a consulta, não
# a ingestão), então é puxado aqui de forma explícita. O nome do modelo vem do
# TOML, nunca literal: `parametros.toml` é a fonte, e o caminho do cache é o
# mesmo que `retriever.py` monta em produção.
RUN python - <<'PY'
from pathlib import Path

from flashrank import Ranker

from copiloto.recuperacao.retriever import carregar_parametros

parametros = carregar_parametros(Path("config/parametros.toml"))
Ranker(model_name=parametros.modelo_rerank, cache_dir=str(Path("indices") / "flashrank"))
PY

# --- 3. observabilidade ------------------------------------------------------
# O `langfuse` é extra, e não dependência principal, porque arrasta o
# OpenTelemetry inteiro e a suíte hermética do CI não pode depender dele. Mas a
# instância implantada é justamente a que emite trace: sem isto, `tracing.py`
# degrada para no-op e o Bicep estaria passando chave para ninguém.
#
# Depois do estágio caro de propósito: mexer na observabilidade não pode custar
# baixar 22 normas do BCB de novo.
FROM corpus AS completo
RUN pip install -e ".[obs]"

# --- 4. runtime --------------------------------------------------------------
# Sem pip, sem compilador, sem `src` duplicado: só a venv e a árvore de `/app`,
# nos mesmos caminhos do estágio anterior — o install editável aponta para
# `/app/src` e mover a árvore o quebraria.
FROM base AS runtime

# Usuário sem privilégio. O `chown` não é cosmético: o Chroma abre
# `indices/chroma/chroma.sqlite3` em leitura E escrita, e `/mnt/estado` é o
# volume onde o checkpointer grava.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin copiloto \
 && mkdir -p /mnt/estado \
 && chown copiloto:copiloto /mnt/estado

COPY --from=completo --chown=copiloto:copiloto /opt/venv /opt/venv
COPY --from=completo --chown=copiloto:copiloto /app /app

USER copiloto

# O checkpoint é a ÚNICA coisa que precisa sobreviver ao scale-to-zero: é ele que
# faz o `interrupt()` do HITL ser retomável depois de o processo morrer. Fica no
# volume; todo o resto da imagem é de leitura.
ENV CHECKPOINT_PATH=/mnt/estado/checkpoints.sqlite \
    LLM_PROVIDER=groq

EXPOSE 8000

# `/saude` nunca devolve 5xx por dependência externa (ver `api/rotas.py`), então
# ele mede o que o probe deve medir: se o processo subiu com índice e catálogo.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4).status == 200 else 1)"

# Um worker só, e isso é decisão, não default: `api/main.py` mantém um
# `threading.Lock` em volta do grafo porque dois workers no mesmo processo
# carregariam dois modelos ONNX — e, aqui, escreveriam no mesmo SQLite sobre SMB.
# A escala horizontal deste desenho é `max_replicas`, e ela está fixada em 1 pelo
# mesmo motivo. Ver o README.
CMD ["uvicorn", "copiloto.api.main:criar_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
