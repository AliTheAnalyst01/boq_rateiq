// Deploys all 4 container apps into an EXISTING Container Apps Environment.
// Qdrant uses Azure Files for vector persistence. PostgreSQL uses ephemeral storage
// (Azure Files SMB does not support chmod, which PostgreSQL initdb requires).

@description('Region of the existing CAE — all container apps must match')
param caeLocation string = 'eastus'

@description('Full ARM resource ID of the existing Container Apps Environment')
param existingEnvId string

@description('CAE storage name for Qdrant (registered via caeStorageReg module)')
param qdrantStorageName string

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


resource postgresApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-postgres'
  location: caeLocation
  properties: {
    environmentId: existingEnvId
    configuration: {
      // TCP ingress — PostgreSQL uses TCP, not HTTP. Internal DNS: rateiq-postgres:5432
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
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource qdrantApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-qdrant'
  location: caeLocation
  properties: {
    environmentId: existingEnvId
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
          storageName: qdrantStorageName
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource redisApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-redis'
  location: caeLocation
  properties: {
    environmentId: existingEnvId
    configuration: {
      // TCP ingress — Redis uses TCP. Internal DNS: rateiq-redis:6379
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
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiAppName
  location: caeLocation
  properties: {
    environmentId: existingEnvId
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
      // NOTE: Secret values here are stored in ARM deployment history (solo project trade-off).
      // Production path: Key Vault references + managed identity.
      secrets: [
        { name: 'acr-password',    value: acrPassword }
        { name: 'anthropic-key',   value: anthropicApiKey }
        { name: 'tavily-key',      value: tavilyApiKey }
        { name: 'openai-key',      value: openaiApiKey }
        // sslmode=disable: internal container-to-container traffic, no TLS needed
        { name: 'postgres-url',    value: 'postgresql://${postgresUser}:${postgresPassword}@rateiq-postgres:5432/${postgresDatabaseName}?sslmode=disable' }
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
            { name: 'QDRANT_URL',        value: 'http://rateiq-qdrant' }
            { name: 'REDIS_URL',         value: 'redis://rateiq-redis:6379' }
            { name: 'POSTGRES_URL',      secretRef: 'postgres-url' }
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-key' }
            { name: 'TAVILY_API_KEY',    secretRef: 'tavily-key' }
            { name: 'OPENAI_API_KEY',    secretRef: 'openai-key' }
            { name: 'ENVIRONMENT',       value: 'production' }
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
