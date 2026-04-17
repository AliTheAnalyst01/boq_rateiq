@description('Azure region where the registry will be created (e.g. eastus)')
param location string

@description('Name of the registry — must be globally unique, alphanumeric only, 5-50 chars')
param acrName string

resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

output loginServer string = acr.properties.loginServer
output acrName string = acr.name
output adminUsername string = acr.listCredentials().username
output adminPassword string = acr.listCredentials().passwords[0].value
