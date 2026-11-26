// The part of Azure the pipeline changes on every deploy: the Container Apps
// environment and one Container App per service. .github/workflows/cd.yml
// deploys it with `az deployment group create`. Deploying it again with the
// same values changes nothing.
//
// The storage account, the SQL server and its two databases are made once by
// infra/setup.sh and only looked up here, because they hold data or need the
// SQL admin password, which the pipeline does not have.

@description('Start of every resource name: the IE username with - instead of .')
@minLength(3)
param prefix string

@minLength(3)
@maxLength(24)
@description('The storage account whose static website serves ui/.')
param uiStorage string

@description('Image of each service in GHCR, tagged with the commit SHA.')
param watchlistImage string
param alertsImage string

@secure()
@description('Password of watchlist_user in the watchlist database.')
param watchlistDbPassword string

@secure()
@description('Password of alerts_user in the alerts database.')
param alertsDbPassword string

@secure()
@description('Signs the sign-in tokens. Both services get the same value.')
param tokenSecret string

@secure()
@description('The Finnhub API key. Only the watchlist service has it.')
param priceApiKey string

param location string = resourceGroup().location

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: uiStorage
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01' existing = {
  name: '${prefix}-sql'
}

// The UI's address, https://<account>.z<N>.web.core.windows.net/. It is the
// only origin the services accept from a browser (CORS). The services drop
// the slash at the end.
var uiOrigin = storage.properties.primaryEndpoints.web
var sqlHost = sqlServer.properties.fullyQualifiedDomainName

// Consumption only: no fixed cost, billed per second while a replica runs.
// It has no Log Analytics workspace, which is not in the allowance (brief
// section 7). Logs are still streamed by `az containerapp logs show`.
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    zoneRedundant: false
  }
}

resource watchlist 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-watchlist'
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      // One revision at a time. A deploy starts the new one, then stops the old.
      activeRevisionsMode: 'Single'
      // Public HTTPS, because the browser calls it. Plain HTTP is redirected.
      ingress: {
        external: true
        targetPort: 8001
        transport: 'auto'
        allowInsecure: false
      }
      // Kept as Container Apps secrets, never as plain environment variables.
      secrets: [
        {
          name: 'database-url'
          value: 'mssql+pymssql://watchlist_user:${uriComponent(watchlistDbPassword)}@${sqlHost}:1433/${prefix}-watchlist-db'
        }
        { name: 'token-secret', value: tokenSecret }
        { name: 'price-api-key', value: priceApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'watchlist'
          image: watchlistImage
          // The smallest size Container Apps offers.
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'TOKEN_SECRET', secretRef: 'token-secret' }
            { name: 'PRICE_API_KEY', secretRef: 'price-api-key' }
            { name: 'CORS_ORIGINS', value: uiOrigin }
            // Every request comes through the Container Apps proxy.
            { name: 'TRUSTED_PROXIES', value: '1' }
          ]
        }
      ]
      // Zero when idle, so nothing is billed. Never more than one, so the
      // migrations at startup never run twice at the same moment.
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

resource alerts 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-alerts'
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8002
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'database-url'
          value: 'mssql+pymssql://alerts_user:${uriComponent(alertsDbPassword)}@${sqlHost}:1433/${prefix}-alerts-db'
        }
        { name: 'token-secret', value: tokenSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'alerts'
          image: alertsImage
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'TOKEN_SECRET', secretRef: 'token-secret' }
            { name: 'CORS_ORIGINS', value: uiOrigin }
            // The watchlist's public address. Naming it here also makes Azure
            // deploy the watchlist first.
            { name: 'WATCHLIST_URL', value: 'https://${watchlist.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

// The pipeline writes these into ui/config.js and calls /health on them.
output watchlistUrl string = 'https://${watchlist.properties.configuration.ingress.fqdn}'
output alertsUrl string = 'https://${alerts.properties.configuration.ingress.fqdn}'
output publicUrl string = uiOrigin
