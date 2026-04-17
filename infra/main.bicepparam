using './main.bicep'

param acrName = 'rateiqacr'
param keyVaultName = 'kv-boq-rateiq'
param pgServerName = 'psql-boq-rateiq'
param pgAdminUser = 'rateiq'
param environmentName = 'cae-boq-rateiq'
param apiAppName = 'rateiq-api'
param imageTag = 'latest'
