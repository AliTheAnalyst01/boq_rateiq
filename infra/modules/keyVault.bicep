@description('Azure region')
param location string

@description('Key Vault name — 3-24 chars, globally unique, alphanumeric + hyphens')
param keyVaultName string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    softDeleteRetentionInDays: 7
    enableSoftDelete: true
  }
}

output keyVaultUri string = kv.properties.vaultUri
output keyVaultName string = kv.name
output keyVaultId string = kv.id
