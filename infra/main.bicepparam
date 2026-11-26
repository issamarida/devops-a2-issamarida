// The values for main.bicep. The CD workflow sets each one as an environment
// variable of its deploy step, so no secret is written in a file or on a
// command line, and nothing about this deployment is fixed in the repository.
using 'main.bicep'

param prefix = readEnvironmentVariable('PREFIX')
param uiStorage = readEnvironmentVariable('UI_STORAGE')
param watchlistImage = readEnvironmentVariable('WATCHLIST_IMAGE')
param alertsImage = readEnvironmentVariable('ALERTS_IMAGE')
param watchlistDbPassword = readEnvironmentVariable('WATCHLIST_DB_PASSWORD')
param alertsDbPassword = readEnvironmentVariable('ALERTS_DB_PASSWORD')
param tokenSecret = readEnvironmentVariable('TOKEN_SECRET')
param priceApiKey = readEnvironmentVariable('PRICE_API_KEY')
