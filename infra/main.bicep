targetScope = 'resourceGroup'

@description('Azure region for ACR, Key Vault, and storage (northeurope = our resource group)')
param location string = resourceGroup().location

@description('Azure Container Registry name (globally unique, alphanumeric only)')
param acrName string

@description('Key Vault name (globally unique, 3-24 chars)')
param keyVaultName string

@description('Name of the EXISTING Container Apps Environment to deploy into')
param existingEnvName string = 'cae-real-estate-chatbot'

@description('Resource group that owns the existing Container Apps Environment')
param existingEnvResourceGroup string = 'rg-real-estate-chatbot'

@description('Region of the existing Container Apps Environment')
param caeLocation string = 'eastus'

@description('FastAPI Container App name')
param apiAppName string = 'rateiq-api'

@description('Docker image tag to deploy (use git SHA in CI/CD)')
param imageTag string = 'latest'

@description('PostgreSQL admin username')
param pgAdminUser string = 'rateiq'

@secure()
@description('PostgreSQL admin password — min 8 chars, uppercase + lowercase + digit + special')
param pgAdminPassword string

@secure()
@description('Anthropic API key (sk-ant-...)')
param anthropicApiKey string

@secure()
@description('Tavily search API key (tvly-...)')
param tavilyApiKey string

@secure()
@description('OpenAI API key (sk-proj-...)')
param openaiApiKey string


// Step 1 — ACR: stores Docker images (stays in resource group region)
module registry 'modules/registry.bicep' = {
  name: 'registry-deployment'
  params: {
    location: location
    acrName: acrName
  }
}

// Step 2 — Key Vault: placeholder for future secret rotation migration
module kv 'modules/keyVault.bicep' = {
  name: 'keyvault-deployment'
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

// Step 3 — Azure Files: persistent storage for Qdrant vectors + Postgres data
module storage 'modules/storage.bicep' = {
  name: 'storage-deployment'
  params: {
    location: caeLocation   // co-locate with CAE for lowest latency volume mounts
  }
}

// Step 4 — Register Azure Files with the existing CAE (runs in rg-real-estate-chatbot scope)
module caeStorage 'modules/caeStorageReg.bicep' = {
  name: 'cae-storage-registration'
  scope: resourceGroup(existingEnvResourceGroup)
  params: {
    existingEnvName: existingEnvName
    storageAccountName: storage.outputs.storageAccountName
    storageAccountKey: storage.outputs.storageAccountKey
  }
}

// Step 5 — Deploy the 4 container apps into the existing CAE
module apps 'modules/containerApps.bicep' = {
  name: 'containerapps-deployment'
  params: {
    caeLocation: caeLocation
    existingEnvId: resourceId(existingEnvResourceGroup, 'Microsoft.App/managedEnvironments', existingEnvName)
    qdrantStorageName: caeStorage.outputs.qdrantStorageName
    postgresStorageName: caeStorage.outputs.postgresStorageName
    apiAppName: apiAppName
    acrLoginServer: registry.outputs.loginServer
    acrUsername: registry.outputs.adminUsername
    acrPassword: registry.outputs.adminPassword
    imageTag: imageTag
    postgresUser: pgAdminUser
    postgresPassword: pgAdminPassword
    anthropicApiKey: anthropicApiKey
    tavilyApiKey: tavilyApiKey
    openaiApiKey: openaiApiKey
  }
}

output apiUrl string = apps.outputs.apiUrl
output acrLoginServer string = registry.outputs.loginServer
output keyVaultName string = kv.outputs.keyVaultName
