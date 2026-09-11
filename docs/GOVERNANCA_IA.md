# Governança de IA — Copiloto Normativo BCB

Núcleo neutro. Vocabulário público, ancorado em frameworks que qualquer leitor pode verificar
por conta própria: **NIST AI RMF**, **ISO/IEC 42001**, **EU AI Act** e **LGPD**. Este documento
não fala de nenhuma plataforma comercial — a ponte para a GABBI e o NEXT.AI da SPREAD está em
[`GABBI_READY.md`](GABBI_READY.md), separado de propósito.

## A tese

Governança de IA costuma significar um documento bonito, escrito uma vez e desatualizado na
segunda mudança de código. Este projeto testa uma tese diferente: **a ficha do sistema é
validada contra o código real, por código, a cada execução.** Se `config/governanca.yaml`
disser algo que `parametros.toml` ou o inventário de provedores não confirmam, o CI fica
vermelho — a mesma lógica que já protege o validador de citação (§8) e a tabela de ablação
(Fase 3). Governança que ninguém verifica é papel.

A prova viva, e qualquer leitor pode reproduzir:

```bash
# mude max_passos em config/parametros.toml — qualquer valor diferente de 8
.venv/Scripts/python.exe -m pytest tests/test_coerencia.py::test_ficha_atual_e_coerente_com_o_codigo
# fica vermelho
# reverta o valor
.venv/Scripts/python.exe -m pytest tests/test_coerencia.py::test_ficha_atual_e_coerente_com_o_codigo
# fica verde
```

## As quatro funções do NIST AI RMF

### GOVERN — quem é responsável e por que existe

`config/governanca.yaml::identificacao` e `::proposito` fixam dono, contato, domínio e os
**casos de uso vedados**: não interpreta a norma, não dá parecer jurídico, não afirma
conformidade. Não são intenção declarada agora — são o que o grafo já recusa a fazer desde a
Fase 5 (`.claude/rules/grafo.md`). A ficha só nomeia o que o código já impõe.

`::classificacao_risco` classifica o sistema como **risco limitado** sob o EU AI Act,
com a justificativa escrita e o framework citado — o valor está em fazer a análise e
defendê-la por escrito, não na resposta que ela dá. Um sistema que decidisse sozinho sobre
crédito, emprego ou justiça estaria no Anexo III; este localiza e cita, e a única escrita
externa passa por aprovação humana.

### MAP — o que o sistema é, de fato

**O achado central desta fase:** o repositório contém **quatro sistemas de IA distintos**,
não um. `governanca/inventario.py` varre o código — não uma lista digitada — e encontra:

| Sistema | Papel | Origem |
|---|---|---|
| `groq` | Responde ao usuário (primário) | `llm/groq.py` |
| `eval-judge/camada-3` | Julga a resposta do sistema acima | `evals/rodar.py` |
| `azure_openai` | Previsto, não implementado | declarado na ficha, ausente no código |
| `ollama` | Previsto, não implementado | declarado na ficha, ausente no código |

O terceiro item da lista é o argumento mais forte da fase: **um LLM que avalia outro LLM é
um sistema de IA em produção.** Tem fornecedor, custo e modo de falha próprios — a mesma Groq,
mas um sistema diferente, com propósito, risco e critério de reavaliação distintos do que ele
julga. É exatamente o tipo de sistema que escapa dos inventários reais, porque ninguém pensa
em "avaliador" como algo que precisa ser governado.

Os outros dois — `azure_openai` e `ollama` — aparecem como **previstos, não
implementados**, e essa é a parte que mais separa esta ficha de um documento de intenção:
`api/main.py::provedor_do_ambiente` falha alto se alguém tentar usá-los, em vez de cair em
silêncio na Groq. A ausência é dado, não lacuna escondida.

### MEASURE — o que é medido, e o que não é

`config/governanca.yaml::metricas` espelha os thresholds de `parametros.toml::[evals]`, com
`onde_medido` apontando para a camada exata de `evals/rodar.py` que produz cada número.
`GET /governanca/metricas` devolve o resultado da **última execução real**, nunca um valor
calculado na hora do request — a régua de `.claude/rules/observabilidade.md` exige que
`recall@5` na ablação e `recall@5` no painel sejam o mesmo número, calculado do mesmo jeito.

**O que este projeto não mede, declarado como tal:** custo real por consulta, latência p95,
taxa de citação órfã em produção e a razão aprovado/rejeitado no HITL exigiriam consultar de
volta a API de leitura do Langfuse — integração não construída nesta fase (o motivo técnico
está no cabeçalho de `governanca/metricas.py`). Aparecem no painel como "não implementado",
com o motivo escrito, nunca como número inventado. Resultado ausente e declarado vale mais
que resultado forjado — é a mesma régua que já levou a Fase 3 a publicar quando o re-ranking
não melhorava um número.

### MANAGE — o que muda quando algo dá errado

`::incidentes` registra o fallback: uma resposta sem base normativa suficiente diz isso
explicitamente, em vez de inventar. `::ciclo_de_vida` fixa os gatilhos que exigem reavaliar a
ficha — troca de provedor primário, mudança de corpus, nova tool de escrita, ou revisão do
material público de terceiros que este projeto cita (ver `GABBI_READY.md`).

## Controles verificados por código

Dois guardrails do §8, cada um com o teste que prova que ele funciona:

| Controle | Tipo | Determinístico | Prova |
|---|---|---|---|
| PII e injeção de prompt | entrada | sim | `tests/test_guardrails.py::test_tentativa_de_sobrescrever_instrucao_bloqueia_a_entrada` |
| Validação de citação | saída | sim | `tests/test_guardrails.py::test_citacao_orfa_reprova_mesmo_com_artigo_plausivel` |

`governanca/coerencia.py::verificar_controles_com_prova` roda os dois testes de verdade a cada
chamada a `GET /governanca/coerencia` — "o teste existe e passa" significa a execução real, não
uma cópia de resultado anterior guardada em algum lugar.

## LGPD

O corpus é normativo público; nenhum dado pessoal é indexado. A única superfície de PII é a
*pergunta* do usuário, e ela é mascarada por regex antes de entrar no estado do grafo ou no
trace (`grafo/guardrails.py::sanear_pergunta`) — CPF, e-mail e telefone nunca chegam a um
LLM de terceiro em claro. `config/governanca.yaml::dados` documenta a base legal e a política
de retenção do checkpoint de conversa.

## O que esta camada deliberadamente não faz

Ver a tabela "O que NÃO foi implementado" do README — as linhas relevantes aqui são
"Cliente HTTP para plataforma de governança" (a integração com uma plataforma real fingiria
um contrato que não existe), "RBAC na governança" (papel de quem consome o inventário, não
de quem o publica) e "Model registry / detecção de drift" (o corpus é estático e o modelo não
é treinado neste projeto — drift sem retreino é métrica sem sujeito).
