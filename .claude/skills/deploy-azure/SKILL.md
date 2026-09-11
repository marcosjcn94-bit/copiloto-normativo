---
name: deploy-azure
description: Roteiro do deploy do copiloto no Azure Container Apps — preparo único da identidade federada, publicação pelo GitHub Actions, e a conferência de custo. Usar quando o pedido envolver deploy, Container Apps, OIDC, Bicep, custo no Azure ou o endpoint público.
---

# Deploy no Azure

O deploy em si é `.github/workflows/deploy.yml` e `infra/main.bicep`. Esta skill é o que
**não** cabe em código: o preparo de uma vez só, e a conferência que fecha a Fase 8.

O CI não substitui nada aqui. `az ad app create` cria identidade; `az role assignment create`
concede permissão. Nenhum dos dois é coisa que um workflow deva poder fazer sozinho.

## 0. O que já está decidido (não reabrir sem número novo)

| Decisão | Por quê | Onde está escrito |
|---|---|---|
| Registry é o GHCR, não o ACR | ACR não tem free tier; Basic é pago por dia | `infra/main.bicep`, cabeçalho |
| Estado é SQLite em Azure Files, não PostgreSQL | Free tier do Postgres dura 12 meses e não é verificável por região | idem, e o README |
| `maxReplicas: 1` | SQLite sobre SMB não tem lock confiável entre máquinas | `main.bicep`, bloco `scale` |
| `minReplicas: 0` | É o que faz o custo ser zero (§10.3) | idem |
| Log Analytics desligado | Ingestão de log é cobrada por GB | parâmetro `comLogAnalytics` |
| Tag da imagem é o SHA | Tag móvel tira do Container Apps a noção de revisão nova | `deploy.yml` |

## 1. Preparo único

Rodar na máquina, com `az login` feito. Substituir `<dono>/<repo>`.

```bash
ASSINATURA=$(az account show --query id -o tsv)
INQUILINO=$(az account show --query tenantId -o tsv)
REPO="<dono>/<repo>"

# Identidade que o GitHub vai assumir. Sem senha: é isso que o OIDC compra (§10.5).
APP=$(az ad app create --display-name copiloto-normativo-deploy --query appId -o tsv)
az ad sp create --id "$APP"

# Contributor na assinatura porque o workflow cria o grupo de recursos. Para
# reduzir o escopo: criar o grupo à mão, dar Contributor só nele e apagar o passo
# "Garantir o grupo de recursos" do deploy.yml.
az role assignment create \
  --assignee "$APP" --role Contributor --scope "/subscriptions/$ASSINATURA"

# UMA credencial, com `subject` de Environment. É por isso que o job `implantar`
# declara `environment: azure`: sem isso o subject seria a ref, e uma tag nova
# exigiria uma credencial nova.
az ad app federated-credential create --id "$APP" --parameters "{
  \"name\": \"github-azure\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${REPO}:environment:azure\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait

echo "AZURE_CLIENT_ID=$APP"
echo "AZURE_TENANT_ID=$INQUILINO"
echo "AZURE_SUBSCRIPTION_ID=$ASSINATURA"
```

No GitHub, em **Settings → Environments**, criar o environment `azure`. Depois, em
**Settings → Secrets and variables → Actions**:

| Tipo | Nome | Valor |
|---|---|---|
| Variable | `AZURE_CLIENT_ID` | o `$APP` impresso acima |
| Variable | `AZURE_TENANT_ID` | o `$INQUILINO` |
| Variable | `AZURE_SUBSCRIPTION_ID` | a `$ASSINATURA` |
| Variable | `AZURE_RESOURCE_GROUP` | `copiloto-normativo` |
| Variable | `AZURE_LOCATION` | `brazilsouth` |
| Secret | `GROQ_API_KEY` | a chave da Groq |
| Secret | `LANGFUSE_PUBLIC_KEY` | opcional — vazio desliga o trace |
| Secret | `LANGFUSE_SECRET_KEY` | opcional |

## 2. Publicar

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Ou **Actions → Deploy → Run workflow**. O workflow roda lint e a suíte hermética antes de
construir, publica a imagem no GHCR, aplica o Bicep e só fica verde depois de bater em
`/saude` e receber 200.

**Na primeira vez, o pacote do GHCR nasce privado.** Em `github.com/<dono>?tab=packages` →
`copiloto-normativo` → *Package settings* → *Change visibility* → **Public**. Sem isso o
Container Apps não puxa a imagem e a revisão fica em `ProvisioningFailed`. Alternativa, se o
pacote tiver de continuar privado: passar `registroUsuario` e `registroSenha` ao Bicep — os
dois parâmetros já existem.

## 3. Conferir o custo (é o que fecha a fase)

O critério de pronto é **R$ 0 confirmado no portal**, não estimado.

1. Portal → **Cost Management → Cost analysis**, escopo no grupo de recursos.
2. Esperar de 8 a 24 h: o Cost Analysis não é tempo real, e olhar logo depois do deploy
   mostra R$ 0 por atraso de telemetria, o que não prova nada.
3. Agrupar por *Service name*. O esperado é Container Apps em zero (franquia) e Storage em
   centavos — o share do `/mnt/estado`.
4. Print no README.

Alerta de orçamento: o Bicep cria, se `emailsDoAlerta` for passado.

```bash
az deployment group create -g copiloto-normativo -f infra/main.bicep \
  -p imagem=<a mesma do último deploy> -p groqApiKey=<chave> \
  -p emailsDoAlerta='["voce@exemplo.com"]' -p tetoDoAlerta=5
```

## 4. Quando algo falha

| Sintoma | Causa quase sempre |
|---|---|
| Revisão em `ProvisioningFailed`, sem log | Pacote do GHCR privado — ver §2 |
| Réplica reinicia em laço | Startup probe curto demais para carregar dois ONNX; ver `probes` no Bicep |
| `/saude` 200 mas resposta diz índice ausente | Build da imagem passou com a coleta do BCB incompleta — reconstruir |
| `database is locked` no checkpoint | Alguém subiu `maxReplicas` acima de 1 |
| Custo saiu de zero | Log Analytics ligado, ou `minReplicas` acima de 0 |

Ver o log sem Log Analytics:

```bash
az containerapp logs show -g copiloto-normativo -n copilotonormativo --follow
```

## 5. Regras

* Segredo nunca entra no Bicep versionado. Entra como `@secure()` e vira secret do Container
  App (§10.4).
* Nenhuma tag móvel. A imagem é sempre `:<sha>`.
* Recurso novo no Bicep exige recalcular o custo antes de aplicar, não depois.
* Não subir `maxReplicas` sem antes trocar o checkpointer.
