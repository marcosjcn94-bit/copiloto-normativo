// Infraestrutura do copiloto no Azure (Fase 8, §10 do briefing).
//
// Escopo: grupo de recursos. Deploy:
//
//   az deployment group create -g <rg> -f infra/main.bicep \
//      -p imagem=ghcr.io/<usuario>/copiloto-normativo:<sha> -p groqApiKey=<chave>
//
// ---------------------------------------------------------------------------
// DUAS ENTREGAS DO §10 FORAM TROCADAS, E AS DUAS POR CAUSA DO MESMO CRITÉRIO
//
// O critério de pronto da Fase 8 é "custo confirmado em R$ 0". Ele derruba duas
// escolhas do §10, e cada troca está registrada no README:
//
// 1. **Sem Azure Container Registry.** O ACR não tem free tier — nem eventual,
//    nem limitado: o Basic é o mais barato e é pago por dia. Um registry pago
//    para hospedar uma imagem de portfólio contradiz o critério da própria fase.
//    A imagem vai para o GitHub Container Registry, gratuito para pacote
//    público, e o Container Apps a puxa anonimamente. `registroUsuario` existe
//    para o caso de o pacote ficar privado.
//
// 2. **Sem PostgreSQL Flexible Server.** É o fallback previsto no §10.2, e ele
//    foi acionado por três motivos somados: a Microsoft não publica em quais
//    regiões o trial está habilitado (o gate é no portal, na criação); o
//    benefício dura 12 meses, enquanto a franquia do Container Apps é
//    permanente — a promessa de R$ 0 quebraria no mês 13; e o checkpointer de
//    hoje é `SqliteSaver`, então trocar exigiria `langgraph-checkpoint-postgres`,
//    dependência fora da lista fechada do §3.
//
// O estado vai para SQLite num Azure Files montado em `/mnt/estado`. É a única
// linha não-zero do desenho: um share de 1 GiB com alguns MB usados custa
// centavos por mês. E é ela que fixa `maxReplicas: 1` — ver o comentário lá
// embaixo, porque essa parte não é detalhe de custo, é de correção.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Prefixo dos nomes. Só letras minúsculas e números.')
@minLength(3)
@maxLength(17)
param nome string = 'copilotonormativo'

@description('Região. O default herda a do grupo de recursos.')
param local string = resourceGroup().location

@description('Imagem completa com tag imutável. Nunca :latest — ver o README.')
param imagem string

@description('Chave da Groq. Vai para os secrets do Container App, nunca para o Bicep versionado (§10.4).')
@secure()
param groqApiKey string

@description('Chave pública do Langfuse. Vazio desliga o trace e o copiloto responde igual.')
param langfusePublicKey string = ''

@description('Chave secreta do Langfuse.')
@secure()
param langfuseSecretKey string = ''

@description('Host do Langfuse Cloud.')
param langfuseHost string = 'https://cloud.langfuse.com'

@description('Usuário do registry. Vazio = imagem pública, puxada sem credencial.')
param registroUsuario string = ''

@description('Token do registry, quando o pacote for privado.')
@secure()
param registroSenha string = ''

@description('Liga o Log Analytics. Fica desligado por padrão: ingestão de log é cobrada por GB e o critério da fase é R$ 0. A observabilidade deste projeto é o Langfuse.')
param comLogAnalytics bool = false

@description('IPs autorizados no ingress, em CIDR. Vazio = aberto à internet. Ver a nota de autenticação no README.')
param ipsAutorizados array = []

@description('E-mails do alerta de orçamento (§10.7). Vazio não cria o alerta.')
param emailsDoAlerta array = []

@description('Teto do alerta de orçamento, na moeda da cobrança.')
param tetoDoAlerta int = 5

@description('Mês inicial do orçamento. Não passar: `utcNow()` só é válido como default de parâmetro.')
param mesDoOrcamento string = utcNow('yyyy-MM')

var sufixo = uniqueString(resourceGroup().id)
var nomeDaConta = take('${nome}${sufixo}', 24)
var nomeDoShare = 'estado'
var comLangfuse = !empty(langfusePublicKey) && !empty(langfuseSecretKey)
var comRegistroPrivado = !empty(registroUsuario)
var servidorDoRegistro = split(imagem, '/')[0]

// --- estado persistente ----------------------------------------------------

resource conta 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: nomeDaConta
  location: local
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource arquivos 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: conta
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: arquivos
  name: nomeDoShare
  properties: {
    // 1 GiB é o mínimo cobrável e sobra: o que mora aqui é um SQLite de
    // checkpoints, que na máquina de desenvolvimento tem 300 KB.
    shareQuota: 1
    enabledProtocols: 'SMB'
    accessTier: 'TransactionOptimized'
  }
}

// --- logs (desligados por padrão) ------------------------------------------

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (comLogAnalytics) {
  name: '${nome}-logs'
  location: local
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    // Sem este teto o custo do ambiente deixa de ser previsível: ingestão de log
    // é cobrada por GB e não há nada no Container Apps que a limite.
    workspaceCapping: { dailyQuotaGb: json('0.1') }
  }
}

// --- ambiente do Container Apps --------------------------------------------

resource ambiente 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${nome}-env'
  location: local
  properties: {
    appLogsConfiguration: comLogAnalytics
      ? {
          destination: 'log-analytics'
          logAnalyticsConfiguration: {
            // O `!` afirma o que o ternário já garante: este ramo só é lido
            // quando `comLogAnalytics` é verdadeiro, que é a mesma condição que
            // cria o workspace. `reference`/`list` dentro de `if()` são avaliados
            // pelo ramo escolhido, e não pelos dois.
            customerId: workspace!.properties.customerId
            sharedKey: workspace!.listKeys().primarySharedKey
          }
        }
      : {
          // Sem workspace o log ainda existe: sai no stdout do container e é
          // legível por `az containerapp logs show --follow`. O que se perde é a
          // retenção e a consulta por KQL, não a visibilidade.
          //
          // `destination` fica de fora de propósito: a API rejeita a string
          // literal 'none' (`AppLogsConfiguration.Destination is invalid`) —
          // omitir a propriedade é o que produz esse mesmo comportamento.
        }
  }
}

resource estado 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: ambiente
  name: nomeDoShare
  properties: {
    azureFile: {
      accountName: conta.name
      accountKey: conta.listKeys().keys[0].value
      shareName: share.name
      // O checkpointer grava. Montar como ReadOnly mataria o HITL: o
      // `interrupt()` não teria onde registrar que parou.
      accessMode: 'ReadWrite'
    }
  }
}

// --- o copiloto ------------------------------------------------------------

resource copiloto 'Microsoft.App/containerApps@2024-03-01' = {
  name: nome
  location: local
  properties: {
    managedEnvironmentId: ambiente.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        ipSecurityRestrictions: [
          for (ip, i) in ipsAutorizados: {
            name: 'permitido-${i}'
            ipAddressRange: ip
            action: 'Allow'
          }
        ]
      }
      secrets: concat(
        [
          { name: 'groq-api-key', value: groqApiKey }
        ],
        comLangfuse ? [ { name: 'langfuse-secret-key', value: langfuseSecretKey } ] : [],
        comRegistroPrivado ? [ { name: 'registro-senha', value: registroSenha } ] : []
      )
      registries: comRegistroPrivado
        ? [
            {
              server: servidorDoRegistro
              username: registroUsuario
              passwordSecretRef: 'registro-senha'
            }
          ]
        : []
    }
    template: {
      containers: [
        {
          name: 'copiloto'
          image: imagem
          resources: {
            // 1 vCPU / 2 GiB. A franquia permanente do Consumption é de 180.000
            // vCPU-s e 360.000 GiB-s por mês, ou seja ~50 h de réplica ACORDADA
            // neste tamanho. Com `minReplicas: 0` um portfólio não chega perto —
            // e sem o scale-to-zero nenhuma dessas contas fecha (§10.3).
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: concat(
            [
              { name: 'LLM_PROVIDER', value: 'groq' }
              { name: 'GROQ_API_KEY', secretRef: 'groq-api-key' }
              // O caminho é o do volume, e é a única variável que o container
              // precisa para que o HITL sobreviva ao scale-to-zero.
              { name: 'CHECKPOINT_PATH', value: '/mnt/estado/checkpoints.sqlite' }
              // Vazio de propósito: o CRM falso é ambiente de demonstração local
              // (`infra/docker-compose.yml`) e não sobe no Azure. Sem ele, a tool
              // de escrita fica indisponível e `/saude` diz isso em vez de mentir.
              { name: 'CRM_BASE_URL', value: '' }
            ],
            comLangfuse
              ? [
                  { name: 'LANGFUSE_PUBLIC_KEY', value: langfusePublicKey }
                  { name: 'LANGFUSE_SECRET_KEY', secretRef: 'langfuse-secret-key' }
                  { name: 'LANGFUSE_HOST', value: langfuseHost }
                  // Separa o que é trace de produção do que é trace da máquina de
                  // desenvolvimento. Sem isto os dois caem no mesmo painel e a
                  // métrica de custo por consulta passa a somar experimento.
                  { name: 'LANGFUSE_ENVIRONMENT', value: 'production' }
                ]
              : []
          )
          volumeMounts: [
            { volumeName: 'estado', mountPath: '/mnt/estado' }
          ]
          probes: [
            {
              // Carregar dois modelos ONNX e abrir o Chroma leva dezenas de
              // segundos. Sem o startup probe generoso, o liveness mata a réplica
              // antes de ela terminar de subir e o cold start vira loop.
              type: 'Startup'
              httpGet: { path: '/saude', port: 8000 }
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: { path: '/saude', port: 8000 }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/saude', port: 8000 }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'estado'
          storageType: 'AzureFile'
          storageName: estado.name
        }
      ]
      scale: {
        // Zero é o que torna o custo zero (§10.3).
        minReplicas: 0
        // **Um, e isso não é economia — é correção.** SQLite sobre SMB não tem
        // lock confiável entre máquinas: duas réplicas gravando no mesmo
        // `checkpoints.sqlite` corrompem o arquivo que sustenta o HITL. Escalar
        // horizontalmente exige trocar o checkpointer, e é exatamente aí que o
        // PostgreSQL volta à mesa — não antes.
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              // O processo serve uma consulta por vez (há um `threading.Lock` em
              // volta do grafo em `api/main.py`, porque dois modelos ONNX no
              // mesmo processo não cabem em 2 GiB). Concorrência alta só criaria
              // fila dentro do container.
              metadata: { concurrentRequests: '4' }
            }
          }
        ]
      }
    }
  }
}

// --- alerta de orçamento (§10.7) -------------------------------------------
// O risco desta arquitetura não é cobrança automática; é esquecimento. O alerta
// é declarativo aqui para não depender de alguém lembrar de criá-lo no portal.

resource orcamento 'Microsoft.Consumption/budgets@2023-05-01' = if (!empty(emailsDoAlerta)) {
  name: '${nome}-teto'
  properties: {
    category: 'Cost'
    amount: tetoDoAlerta
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '${mesDoOrcamento}-01T00:00:00Z'
    }
    notifications: {
      metade: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: emailsDoAlerta
        thresholdType: 'Actual'
      }
      teto: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: emailsDoAlerta
        thresholdType: 'Actual'
      }
    }
  }
}

output fqdn string = copiloto.properties.configuration.ingress.fqdn
output saude string = 'https://${copiloto.properties.configuration.ingress.fqdn}/saude'
output docs string = 'https://${copiloto.properties.configuration.ingress.fqdn}/docs'
