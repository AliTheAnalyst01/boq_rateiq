@description('Azure region')
param location string

@description('Container Apps Environment name')
param environmentName string

@description('API Container App name')
param apiAppName string = 'rateiq-api'

@description('ACR login server URL (e.g. rateiqacr.azurecr.io)')
param acrLoginServer string

@description('ACR admin username')
@secure()
param acrUsername string

@description('ACR admin password')
@secure()
param acrPassword string

@description('Docker image tag to deploy (use git SHA for traceability)')
param imageTag string = 'latest'

@description('PostgreSQL admin username')
param postgresUser string = 'rateiq'

@secure()
@description('PostgreSQL admin password')
param postgresPassword string

@description('PostgreSQL database name')
param postgresDatabaseName string = 'rateiq'

@secure()
@description('Anthropic API key')
param anthropicApiKey string

@secure()
@description('Tavily API key')
param tavilyApiKey string

@secure()
@description('OpenAI API key')
param openaiApiKey string


resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'law-${environmentName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'strateiq${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource qdrantShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: 'qdrant-storage'
  properties: { shareQuota: 10 }
}

resource postgresShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: 'postgres-data'
  properties: { shareQuota: 10 }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource caeQdrantStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'qdrant-files'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: qdrantShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource caePostgresStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'postgres-files'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: postgresShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource postgresApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-postgres'
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      // TCP ingress — PostgreSQL is a TCP protocol, not HTTP.
      // Internal DNS: rateiq-postgres:5432 resolves within the CAE environment.
      ingress: {
        external: false
        targetPort: 5432
        transport: 'tcp'
      }
      secrets: [
        { name: 'pg-password', value: postgresPassword }
      ]
    }
    template: {
      containers: [
        {
          name: 'postgres'
          image: 'postgres:15'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'POSTGRES_DB',       value: postgresDatabaseName }
            { name: 'POSTGRES_USER',     value: postgresUser }
            { name: 'POSTGRES_PASSWORD', secretRef: 'pg-password' }
            { name: 'PGDATA',            value: '/var/lib/postgresql/data/pgdata' }
          ]
          volumeMounts: [
            {
              volumeName: 'postgres-data'
              mountPath: '/var/lib/postgresql/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'postgres-data'
          storageType: 'AzureFile'
          storageName: 'postgres-files'
        }
      ]
      scale: {
        minReplicas: 1  // PostgreSQL is stateful — must not scale to zero
        maxReplicas: 1
      }
    }
  }
  dependsOn: [caePostgresStorage]
}

resource qdrantApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-qdrant'
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      ingress: {
        external: false
        targetPort: 6333
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'qdrant'
          image: 'qdrant/qdrant:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          volumeMounts: [
            {
              volumeName: 'qdrant-data'
              mountPath: '/qdrant/storage'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'qdrant-data'
          storageType: 'AzureFile'
          storageName: 'qdrant-files'
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [caeQdrantStorage]
}

resource redisApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-redis'
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      // TCP ingress required for non-HTTP services within the CAE environment.
      // Redis uses TCP; internal DNS (rateiq-redis:6379) resolves within the same environment.
      ingress: {
        external: false
        targetPort: 6379
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: 'redis:7-alpine'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1  // Redis is stateful — scale-to-zero loses in-memory data and causes cold-start delay
        maxReplicas: 1
      }
    }
  }
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiAppName
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      // NOTE: Secret values passed inline here are stored in ARM deployment history.
      // Anyone with Microsoft.Resources/deployments/read on this resource group can read them.
      // For enterprise/production: store secrets in Key Vault and reference via keyVaultUrl + managed identity.
      // Acceptable for solo/small-team projects where the deployer controls resource group access.
      secrets: [
        { name: 'acr-password',          value: acrPassword }
        { name: 'anthropic-key',          value: anthropicApiKey }
        { name: 'tavily-key',             value: tavilyApiKey }
        { name: 'openai-key',             value: openaiApiKey }
        // sslmode=disable: internal container-to-container traffic within the CAE — TLS not needed
        { name: 'postgres-url',           value: 'postgresql://${postgresUser}:${postgresPassword}@rateiq-postgres:5432/${postgresDatabaseName}?sslmode=disable' }
      ]
    }
    template: {
      containers: [
        {
          name: 'rateiq-api'
          image: '${acrLoginServer}/rateiq-api:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'QDRANT_URL',         value: 'http://rateiq-qdrant' }
            { name: 'REDIS_URL',          value: 'redis://rateiq-redis:6379' }
            { name: 'POSTGRES_URL',       secretRef: 'postgres-url' }
            { name: 'ANTHROPIC_API_KEY',  secretRef: 'anthropic-key' }
            { name: 'TAVILY_API_KEY',     secretRef: 'tavily-key' }
            { name: 'OPENAI_API_KEY',     secretRef: 'openai-key' }
            { name: 'ENVIRONMENT',        value: 'production' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
  dependsOn: [postgresApp, qdrantApp, redisApp]
}

output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output apiAppName string = apiApp.name
output environmentName string = cae.name
