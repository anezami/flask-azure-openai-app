# Azure Web App deployment (Bicep)

This folder contains a Bicep template to provision:
- Azure App Service Plan (Linux)
- Azure Web App (Python 3.11) with System Managed Identity
- Azure Key Vault (RBAC enabled) to store Google Client ID and Secret
- Easy Auth (authsettingsV2) configured for Google as the identity provider
- App settings for the Flask app including Key Vault references for Google secrets

## Files
- `main.bicep`: Core infrastructure.
- `azuredeploy.parameters.json`: Sample parameters with placeholders.

## Required values
- `appName`: globally unique web app name.
- `location`: Azure region, e.g., `westeurope`.
- `googleClientId`: Client ID from Google OAuth credentials.
- `googleClientSecret`: Client Secret from Google OAuth credentials (stored in Key Vault).
- `keyVaultName`: globally unique Key Vault name (within the tenant).
- `allowedEmails`: Optional comma-separated allowlist; leave empty to allow all authenticated users.

## Deploy (what-if first)

```powershell
# Login and select subscription
az login
az account set -s <SUBSCRIPTION_ID>

# Create RG if needed
$rg="<RESOURCE_GROUP>"
$loc="westeurope"
az group create -n $rg -l $loc

# Validate (what-if)
az deployment group what-if `
  -g $rg `
  -n webapp-google-auth `
  --template-file ./infra/main.bicep `
  --parameters @./infra/azuredeploy.parameters.json

# Deploy
az deployment group create `
  -g $rg `
  -n webapp-google-auth `
  --template-file ./infra/main.bicep `
  --parameters @./infra/azuredeploy.parameters.json
```

## After deployment
The Google client secret and client ID are stored in Key Vault and injected via Key Vault references; no manual secret setting is needed.

## Zip deploy application code
See `../scripts/deploy.ps1` for an end-to-end flow that provisions infra, assigns AOAI RBAC, and deploys code.
