#!/usr/bin/env bash
# The one-time Azure setup, done once by hand before the first deploy. After
# this only the pipeline changes Azure (.github/workflows/cd.yml with
# infra/main.bicep). The steps follow the course's Azure guide, steps 1 to 7
# and 9. The README explains each one.
#
# Run it from the repository root, in bash, after `az login` and `gh auth login`:
#     bash infra/setup.sh
# It needs az, gh, openssl, curl and Docker (sqlcmd runs from the SQL Server image).
#
# No password is ever written to a file or printed. Each one is made up here,
# handed to Azure and to `gh secret set`, and forgotten when the script ends.
# The SQL admin password is not kept anywhere: if it is ever needed again,
# `az sql server update --admin-password` sets a new one.
set -euo pipefail

STUDENT=iaiarida.ieu2024
PREFIX=iaiarida-ieu2024
REGION=swedencentral
RG=rg-$PREFIX
STORAGE=iaiaridaieu2024ui
SQL_SERVER=$PREFIX-sql
SQL_ADMIN=sqladmin
IDENTITY=$PREFIX-github
REPO=issamarida/devops-a2-issamarida
PROFESSORS="amartini@faculty.ie.edu bserra@faculty.ie.edu"

# 40 letters and digits with at least one upper case letter, one lower case
# letter and one digit, which is what Azure SQL asks of a password.
new_password() {
  local password=""
  until [[ $password =~ [A-Z] && $password =~ [a-z] && $password =~ [0-9] ]]; do
    password=$(openssl rand -base64 60 | tr -dc 'A-Za-z0-9' | head -c 40)
  done
  printf '%s' "$password"
}

echo "1. Subscription. The policy lists the regions this subscription may use."
az account set --subscription "Azure for Students"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
az policy assignment list \
  --query "[?displayName=='Allowed resource deployment regions'].parameters.listOfAllowedLocations.value" -o tsv

echo "2. Resource providers and the resource group."
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.Sql Microsoft.Storage Microsoft.ManagedIdentity; do
  az provider register --namespace "$provider" --wait
done
az group create --name "$RG" --location "$REGION" --output none

echo "3. The storage account and its static website, which serves ui/."
az storage account create --name "$STORAGE" --resource-group "$RG" --location "$REGION" \
  --kind StorageV2 --sku Standard_LRS --min-tls-version TLS1_2 \
  --allow-blob-public-access false --output none
# Turning the website on is a setting inside the account. Its key is used
# here because the subscription owner has no data role on the account.
az storage blob service-properties update --account-name "$STORAGE" --auth-mode key \
  --static-website --index-document index.html --output none
PUBLIC_URL=$(az storage account show --name "$STORAGE" --resource-group "$RG" \
  --query primaryEndpoints.web -o tsv)
echo "   The system's public address is $PUBLIC_URL"

echo "4. The SQL server and one serverless database per service, on the free offer."
SQL_ADMIN_PASSWORD=$(new_password)
az sql server create --name "$SQL_SERVER" --resource-group "$RG" --location "$REGION" \
  --admin-user "$SQL_ADMIN" --admin-password "$SQL_ADMIN_PASSWORD" \
  --minimal-tls-version 1.2 --output none
# 0.0.0.0 is Azure's way of writing "any Azure service", Container Apps included.
az sql server firewall-rule create --server "$SQL_SERVER" --resource-group "$RG" \
  --name AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 --output none
for service in watchlist alerts; do
  # The free offer fixes the pause after 60 minutes and the size at 32 GB,
  # so --auto-pause-delay and --max-size must not be given.
  az sql db create --server "$SQL_SERVER" --resource-group "$RG" --name "$PREFIX-$service-db" \
    --edition GeneralPurpose --compute-model Serverless --family Gen5 \
    --min-capacity 0.5 --capacity 1 --backup-storage-redundancy Local \
    --use-free-limit true --free-limit-exhaustion-behavior AutoPause --output none
done

echo "   This machine may reach the server until the users exist."
MY_IP=$(curl --silent --fail https://api.ipify.org)
az sql server firewall-rule create --server "$SQL_SERVER" --resource-group "$RG" \
  --name setup-machine --start-ip-address "$MY_IP" --end-ip-address "$MY_IP" --output none
# sqlcmd reads its own password from SQLCMDPASSWORD and fills $(SERVICE_USER)
# and $(SERVICE_PASSWORD) in the script from the environment. `docker run -e NAME`
# passes each variable without its value appearing on a command line.
export SQLCMDPASSWORD=$SQL_ADMIN_PASSWORD SERVICE_USER SERVICE_PASSWORD
for service in watchlist alerts; do
  SERVICE_USER=${service}_user
  SERVICE_PASSWORD=$(new_password)
  docker run --rm -e SQLCMDPASSWORD -e SERVICE_USER -e SERVICE_PASSWORD \
    -v "$PWD/db:/db:ro" mcr.microsoft.com/mssql/server:2022-latest \
    /opt/mssql-tools18/bin/sqlcmd -S "tcp:$SQL_SERVER.database.windows.net,1433" \
    -d "$PREFIX-$service-db" -U "$SQL_ADMIN" -N -b -l 60 -i /db/azure-users.sql
  printf '%s' "$SERVICE_PASSWORD" | gh secret set "${service^^}_DB_PASSWORD" --repo "$REPO"
done
unset SQLCMDPASSWORD SQL_ADMIN_PASSWORD SERVICE_PASSWORD
az sql server firewall-rule delete --server "$SQL_SERVER" --resource-group "$RG" --name setup-machine

echo "6. The pipeline's identity, trusted for runs on main of this repository only."
az identity create --name "$IDENTITY" --resource-group "$RG" --location "$REGION" --output none
CLIENT_ID=$(az identity show --name "$IDENTITY" --resource-group "$RG" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show --name "$IDENTITY" --resource-group "$RG" --query principalId -o tsv)
RG_ID=$(az group show --name "$RG" --query id -o tsv)
STORAGE_ID=$(az storage account show --name "$STORAGE" --resource-group "$RG" --query id -o tsv)
# Contributor on the resource group only: it can deploy the apps, nothing outside.
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role Contributor --scope "$RG_ID" --output none
# Contributor manages the account but cannot write files into it. This role,
# on this one account, lets the pipeline upload ui/ to $web.
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" --output none
# GitHub may name the repository by its numeric IDs instead of its name, so
# both forms of the subject are registered (guide step 6).
OWNER_ID=$(gh api "repos/$REPO" --jq .owner.id)
REPO_ID=$(gh api "repos/$REPO" --jq .id)
for subject in "repo:${REPO}:ref:refs/heads/main" \
               "repo:${REPO%%/*}@${OWNER_ID}/${REPO##*/}@${REPO_ID}:ref:refs/heads/main"; do
  name=github-main-$( [[ $subject == *@* ]] && echo ids || echo names )
  az identity federated-credential create --identity-name "$IDENTITY" --resource-group "$RG" \
    --name "$name" --issuer https://token.actions.githubusercontent.com \
    --subject "$subject" --audiences api://AzureADTokenExchange --output none
done

echo "7. GitHub secrets and variables for the pipeline."
printf '%s' "$CLIENT_ID" | gh secret set AZURE_CLIENT_ID --repo "$REPO"
printf '%s' "$TENANT_ID" | gh secret set AZURE_TENANT_ID --repo "$REPO"
printf '%s' "$SUBSCRIPTION_ID" | gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO"
# Signs the sign-in tokens. Both services get the same value.
new_password | gh secret set TOKEN_SECRET --repo "$REPO"
# The Finnhub key is read from the git-ignored .env, never typed or printed.
grep '^PRICE_API_KEY=' .env | cut -d= -f2- | tr -d '\r\n' | gh secret set PRICE_API_KEY --repo "$REPO"
gh variable set PREFIX --body "$PREFIX" --repo "$REPO"
gh variable set RESOURCE_GROUP --body "$RG" --repo "$REPO"
gh variable set UI_STORAGE --body "$STORAGE" --repo "$REPO"

echo "9. Read access to the resource group for the professors."
for professor in $PROFESSORS; do
  az role assignment create --assignee "$professor" --role Reader --scope "$RG_ID" --output none
done

echo "Done. Student $STUDENT, public address $PUBLIC_URL"
