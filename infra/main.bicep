targetScope = 'resourceGroup'

@description('Azure region — defaults to the resource group region')
param location string = resourceGroup().location

@description('Azure Container Registry name (globally unique, alphanumeric only)')
param acrName string

@description('Key Vault name (globally unique, 3-24 chars)')
param keyVaultName string

@description('PostgreSQL server name (globally unique)')
param pgServerName string

@description('PostgreSQL admin username')
param pgAdminUser string = 'rateiq'

@description('Container Apps Environment name')
param environmentName string

@description('FastAPI Container App name')
param apiAppName string = 'rateiq-api'

@description('Docker image tag to deploy')
param imageTag string = 'latest'

@secure()
param pgAdminPassword string

@secure()
param anthropicApiKey string

@secure()
param tavilyApiKey string

@secure()
param openaiApiKey string


module registry 'modules/registry.bicep' = {
  name: 'registry-deployment'
  params: {
    location: location
    acrName: acrName
  }
}

module kv 'modules/keyVault.bicep' = {
  name: 'keyvault-deployment'
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres-deployment'
  params: {
    location: location
    serverName: pgServerName
    adminUser: pgAdminUser
    adminPassword: pgAdminPassword
  }
}

module apps 'modules/containerApps.bicep' = {
  name: 'containerapps-deployment'
  params: {
    location: location
    environmentName: environmentName
    apiAppName: apiAppName
    acrLoginServer: registry.outputs.loginServer
    acrUsername: registry.outputs.adminUsername
    acrPassword: registry.outputs.adminPassword
    imageTag: imageTag
    postgresFqdn: postgres.outputs.serverFqdn
    postgresUser: pgAdminUser
    postgresPassword: pgAdminPassword
    postgresDatabaseName: postgres.outputs.databaseName
    anthropicApiKey: anthropicApiKey
    tavilyApiKey: tavilyApiKey
    openaiApiKey: openaiApiKey
  }
}

output apiUrl string = apps.outputs.apiUrl
output acrLoginServer string = registry.outputs.loginServer
output keyVaultName string = kv.outputs.keyVaultName
output pgServerFqdn string = postgres.outputs.serverFqdn
