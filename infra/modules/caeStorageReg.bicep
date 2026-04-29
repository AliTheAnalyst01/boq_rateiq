// This module runs scoped to the resource group that owns the existing CAE.
// It registers Azure File shares so container apps can mount them as volumes.

@description('Name of the existing Container Apps Environment')
param existingEnvName string

@description('Storage account name (from storage module output)')
param storageAccountName string

@secure()
@description('Storage account key (from storage module output)')
param storageAccountKey string

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: existingEnvName
}

resource qdrantStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'rateiq-qdrant'
  properties: {
    azureFile: {
      accountName: storageAccountName
      accountKey: storageAccountKey
      shareName: 'qdrant-storage'
      accessMode: 'ReadWrite'
    }
  }
}

resource postgresStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'rateiq-postgres'
  properties: {
    azureFile: {
      accountName: storageAccountName
      accountKey: storageAccountKey
      shareName: 'postgres-data'
      accessMode: 'ReadWrite'
    }
  }
}

output qdrantStorageName string = qdrantStorage.name
output postgresStorageName string = postgresStorage.name
