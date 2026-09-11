---
name: gerar-painel-governanca
description: Regera governanca-out/painel.html e governanca-out/inventario.json a partir da ficha, do código e do último evals/rodar.py, e confere a coerência antes de comitar. Usar depois de qualquer mudança em config/governanca.yaml, em parametros.toml ou depois de rodar evals/rodar.py.
---

# Gerar o painel de governança

`governanca/exportador.py` é o código; esta skill é a rotina de várias etapas em volta dele
(§11 do CLAUDE.md — rotina de várias etapas vira skill, não texto no README).

## Quando rodar

* Depois de editar `config/governanca.yaml`.
* Depois de mudar qualquer valor em `config/parametros.toml` que a ficha declara
  (`[grafo]`, `[llm]`, `[evals]`).
* Depois de `evals/rodar.py --todas` — o painel só é honesto se `governanca-out/inventario.json`
  vier da mesma execução que `evals/resultados.json`.
* Antes de comitar qualquer uma das três mudanças acima.

## Passo a passo

```bash
# 1. Confere coerência primeiro — se isto voltar 409, corrigir a ficha antes de gerar nada.
.venv/Scripts/python.exe -c "
from copiloto.governanca.coerencia import verificar
divergencias = verificar()
print('coerente' if not divergencias else divergencias)
"

# 2. Gera os dois artefatos numa leitura só.
.venv/Scripts/python.exe -m copiloto.governanca.exportador

# 3. Confere que o painel abre sem rede: nenhuma referência externa.
grep -E "cdn\.|https://|http://|<script" governanca-out/painel.html && echo "FALHOU: referência externa" || echo "ok, abre offline"
```

## A prova viva (§16, critério de pronto nº 2)

Depois de gerar o painel uma vez, rodar esta demonstração à mão e ver acontecer — é o que se
conta em entrevista:

```bash
# mude max_passos em config/parametros.toml para qualquer valor diferente de 8
.venv/Scripts/python.exe -m pytest tests/test_coerencia.py::test_ficha_atual_e_coerente_com_o_codigo
# vermelho
# reverta o valor
.venv/Scripts/python.exe -m pytest tests/test_coerencia.py::test_ficha_atual_e_coerente_com_o_codigo
# verde de novo
```

## O que não fazer

* Não editar `governanca-out/painel.html` ou `governanca-out/inventario.json` à mão — o próximo
  `exportador.py` sobrescreve, e um número editado ali deixa de ter execução por trás (a
  mesma regra do `relatorio.md` dos evals).
* Não comitar o painel gerado sobre um `evals/resultados.json` velho depois de rodar
  `evals/rodar.py --todas` de novo — regenerar é o passo 2, não opcional.
