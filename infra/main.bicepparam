using './main.bicep'

param acrName = 'rateiqacr'
param keyVaultName = 'kv-boq-rateiq'
param pgServerName = 'psql-boq-rateiq'
param pgAdminUser = 'rateiq'
param environmentName = 'cae-boq-rateiq'
param apiAppName = 'rateiq-api'
param imageTag = 'latest'

// @secure() params — never commit values to source control.
// Supply at deploy time using one of:
//   az deployment group create ... --parameters pgAdminPassword="..." anthropicApiKey="..." tavilyApiKey="..." openaiApiKey="..."
//   or via a local override file (e.g. main.local.bicepparam) excluded from .gitignore
//
// param pgAdminPassword = ''       // Strong password for PostgreSQL admin
// param anthropicApiKey = ''       // sk-ant-...
// param tavilyApiKey = ''          // tvly-...
// param openaiApiKey = ''          // sk-proj-...
