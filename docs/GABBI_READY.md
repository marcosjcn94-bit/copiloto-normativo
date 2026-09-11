# GABBI-ready — ponte para o NEXT.AI da SPREAD

> **Aviso explícito.** O mapeamento abaixo foi inferido do material público de
> `spread.com.br/next-ai/` (releitura em 11/09/2026, ver `BRIEFING_COPILOTO_NORMATIVO.md`
> §15, item 4). **Não há API oficial documentada da GABBI e nenhuma integração real é feita
> neste projeto.** Este documento não afirma compatibilidade nem contrato — afirma que a
> *estrutura* de governança já construída (`docs/GOVERNANCA_IA.md`, `config/governanca.yaml`,
> `governanca/`) se encaixaria nos pilares e nas capacidades que a SPREAD descreve
> publicamente. O aviso não enfraquece o documento; é o que o torna confiável.
>
> A página oficial ficou atrás de um desafio anti-bot durante a releitura (11/09/2026) e não
> foi acessada diretamente; o texto abaixo se apoia em buscas que indexaram o conteúdo
> público da página. Se o vocabulário do NEXT.AI ou da GABBI mudar, este é o arquivo a
> atualizar — `docs/GOVERNANCA_IA.md` não muda, porque é ancorado em normas públicas, não em
> material de terceiro.

## Os cinco pilares do NEXT.AI

| Pilar | O que este projeto tem |
|---|---|
| **Strategy & Alignment** | `config/governanca.yaml::proposito` e `::classificacao_risco` — domínio, público-alvo, casos de uso vedados e a classificação de risco justificada por escrito |
| **Governance & Compliance** | `config/governanca.yaml::autonomia`, `::controles[]` e `docs/GOVERNANCA_IA.md` — nível de autonomia, guardrails com prova de teste, mapeamento a NIST AI RMF / EU AI Act / LGPD |
| **Operations** | `governanca/coerencia.py` + `GET /governanca/coerencia` — a ficha validada contra o código real a cada chamada, não um documento estático |
| **Observability & Monitoring** | Langfuse (Fase 7) com `id_sistema` e `versao_ficha` em todo trace, mais `GET /governanca/metricas` |
| **Value Generation** | `governanca/metricas.py::roi_estimado` — com a distinção explícita entre premissa declarada e medição real, ver a seção de ROI abaixo |

## As capacidades públicas da GABBI

A leitura pública descreve a GABBI conectando visibilidade, controle, monitoramento,
conformidade e segurança para escalar IA "com segurança e previsibilidade". Mapeamento:

| Capacidade pública da GABBI | Artefato deste projeto |
|---|---|
| Visibilidade completa de modelos, agentes e aplicações de IA em uso | `GET /governanca/inventario` — a frota real, varrida do código (§16.1), não uma planilha mantida à mão |
| Políticas, papéis e controles para uso ético, seguro e em conformidade | `config/governanca.yaml::controles[]`, cada um com o teste que prova que funciona |
| Monitoramento contínuo de performance, comportamento, custo e risco | Langfuse por trace (Fase 7) + `GET /governanca/metricas`, com o que não é medido declarado como tal, não inventado |
| Rastreabilidade, auditoria e conformidade com normas e regulações | `docs/GOVERNANCA_IA.md`, ancorado em NIST AI RMF / ISO 42001 / EU AI Act / LGPD |
| Mensuração de ROI | `governanca/metricas.py::roi_estimado` |

## O achado que mais interessaria a uma plataforma de inventário

`eval-judge/camada-3` — o LLM que julga a resposta do LLM primário (`evals/rodar.py`) — é um
sistema de IA em produção que a maioria dos inventários reais não captura, porque ninguém
pensa em "avaliador" como algo a governar. Ele tem fornecedor, custo e modo de falha próprios,
distintos do sistema que ele julga. Uma plataforma de inventário de frota que só enxergasse
"o modelo que atende o cliente" perderia metade do que está de fato rodando aqui.

## ROI — a distinção que este projeto insiste em manter

`governanca/metricas.py::roi_estimado` devolve dois números com rótulos diferentes de
propósito:

* **Premissa declarada:** minutos de busca manual em norma, estimados, nunca medidos.
* **Medição real, quando existir:** custo por consulta a partir do Langfuse — **não
  implementado nesta fase** (ver `docs/GOVERNANCA_IA.md`, seção MEASURE, e o cabeçalho de
  `governanca/metricas.py` para o motivo técnico).

Premissa marcada como premissa é engenharia. Premissa disfarçada de número contradiz o que a
tabela de ablação da Fase 3 já defendeu quando publicou que o re-ranking não melhorava um
número — resultado honesto vale mais que alegação sem medição, e vale aqui do mesmo jeito.

## O que este documento não é

Não é um cliente da API da GABBI — não existe cliente HTTP para nenhuma plataforma de
governança neste repositório (ver "O que NÃO foi implementado" no README, e por que: o
contrato não é público, e fingir integração real é detectável em 30 segundos). É a prova de
que a estrutura de governança construída aqui fala o vocabulário de uma plataforma real, sem
fingir que fala com uma.
