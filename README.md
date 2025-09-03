# ChatGPTKZ2

## Deploy to Azure Web App (with Google Auth)

This repo includes infrastructure as code (Bicep) and a PowerShell script to provision and deploy an Azure Linux Web App with built-in authentication configured for Google, and an Azure Key Vault to store Google credentials. The script also grants the web app’s system-managed identity access to your Azure OpenAI resource.

### Prerequisites
- Azure CLI installed and logged in: `az login`
- Subscription ID
- A Google OAuth Client (Client ID and Client Secret).
	- Authorized redirect URI: `https://<app-name>.azurewebsites.net/.auth/login/google/callback`

### One-time: Provision infra + deploy code

1) Optional: Customize `infra/azuredeploy.parameters.json` with your values. The script also accepts parameters directly and generates a parameters file on the fly.

2) Run the deployment script from repo root (PowerShell):

```powershell
$sub = "<SUBSCRIPTION_ID>"
$rg  = "<RESOURCE_GROUP>"
$loc = "westeurope"
$app = "<your-unique-app-name>"

$googleClientId = "<GOOGLE_CLIENT_ID>"
$googleClientSecret = "<GOOGLE_CLIENT_SECRET>"  # Stored into Key Vault automatically
$keyVaultName = "<unique-kv-name>"

$aoaiEndpoint = "<https://your-aoai-endpoint.openai.azure.com>"
$aoaiDeployment = "<your-aoai-deployment>"
# Optional: if you know the exact Azure OpenAI resource ID, set it for deterministic RBAC assignment
$aoaiResourceId = "/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.CognitiveServices/accounts/<AOAI_ACCOUNT_NAME>"

./scripts/deploy.ps1 `
	-SubscriptionId $sub `
	-ResourceGroup $rg `
	-Location $loc `
	-AppName $app `
	-GoogleClientId $googleClientId `
	-GoogleClientSecret $googleClientSecret `
	-KeyVaultName $keyVaultName `
	-AzureOpenAIEndpoint $aoaiEndpoint `
	-AzureOpenAIDeployment $aoaiDeployment `
    -AzureOpenAIResourceId $aoaiResourceId `
	-AllowedEmails "user1@example.com,user2@example.com" `
	-Sku B1
```

Re-running the script is safe (idempotent). It uses ARM/Bicep deployment and `az webapp deploy` (OneDeploy) for the app code.

### Secrets & RBAC
- Google Client ID and Secret are stored in Key Vault and injected via Key Vault references (`GOOGLE_CLIENT_ID`, `GOOGLE_PROVIDER_AUTHENTICATION_SECRET`).
- The web app’s system-assigned identity is granted Key Vault Secrets User on the new vault.
- The script attempts to assign the web app identity the `Cognitive Services OpenAI User` RBAC role on your Azure OpenAI resource. Provide `-AzureOpenAIResourceId` for the most reliable assignment; otherwise it will try to infer the resource from the endpoint or by listing in the resource group.

# Text Assistant (Grammar & Translation) — Flask + Azure OpenAI (Managed Identity)

Single-page Flask app for grammar correction and translation powered by Azure OpenAI (GPT-4o). The app trusts Azure App Service built-in authentication (e.g., Google, Microsoft Entra ID) and authenticates to Azure OpenAI using System Assigned Managed Identity via `DefaultAzureCredential`.

Note: The legacy `flask-azure-openai-app` subfolder has been removed. The app at the repository root is the single source of truth.

## Features
- Single-page UI with:
	- Text input and file upload (`.txt`, `.md`, `.docx`, `.pdf`)
	- Mode selection dropdown: `Grammar Check` (default) or `Translation`
	- Auto-detect source language; in Translation mode, choose a target language
	- Language selector (UI locale): English/German
- Azure OpenAI GPT-4o via the official `openai` SDK (Azure endpoint)
- Chunking for large inputs to respect token budgets
- No custom auth code: App trusts Azure App Service auth headers
- Ready for Azure App Service Linux with CI/CD via GitHub Actions

## Prerequisites
- Azure subscription with permission to create Resource Groups, App Service Plans, and Web Apps
- Azure CLI installed and logged in (`az login`)
- GitHub account and repository (for CI/CD)
- An Azure OpenAI resource and a model deployment name (e.g., `gpt-4o`)

## Environment Variables
Create a `.env` in the repo root for local development or configure App Settings in Azure:

```
# Flask
FLASK_SECRET_KEY=your-random-secret

# Azure OpenAI (Managed Identity)
AZURE_OPENAI_ENDPOINT=https://<your-ai-foundry-endpoint>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-06-01  # default used by app if unset; you can also use newer preview versions

# Model/runtime tuning (optional)
AOAI_TEMPERATURE=0.2
AOAI_HTTP_TIMEOUT=60
MAX_INPUT_TOKENS=12000
MAX_OUTPUT_TOKENS=2048
TIKTOKEN_ENCODING=o200k_base

# UI
UI_LANG=en  # default UI language if session not set (en|de)
```

No API keys are required. The Web App will use its System Assigned Managed Identity to call Azure OpenAI. Grant this identity access in Azure AI Foundry (Cognitive Services User role on the resource).

## Run Locally (Windows PowerShell)
```pwsh
# From repo root
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Local env; `az login` recommended so DefaultAzureCredential can acquire a token
$env:AZURE_OPENAI_ENDPOINT="https://<your-ai-foundry-endpoint>.openai.azure.com/"
$env:AZURE_OPENAI_DEPLOYMENT="gpt-4o"
$env:AZURE_OPENAI_API_VERSION="2024-06-01"

python .\scripts\smoke_test.py
python app.py
```
Browse `http://localhost:8000`.

Optional live Azure OpenAI test (uses your current `az login` context):
```pwsh
python .\scripts\live_test_aoai.py
```

## Azure Web App Built-in Authentication
Authentication is configured automatically by the Bicep template for Google (Easy Auth v2). Ensure your Google OAuth app has the authorized redirect URI set to `https://<app-name>.azurewebsites.net/.auth/login/google/callback`.

At runtime, the app reads user info from `X-MS-CLIENT-PRINCIPAL` headers injected by App Service. Access is further restricted by the `ALLOWED_EMAILS` app setting (a comma-separated allowlist), which the script can set for you via `-AllowedEmails`.

## Deploy to Azure (Script)
Use the provided PowerShell script to provision (or reuse) resources and deploy the root app:

```pwsh
./scripts/deploy.ps1 `
	-SubscriptionId <SUB_ID> `
	-ResourceGroup <RG_NAME> `
	-Location westeurope `
	-AppName <APP_NAME> `
	-AzureOpenAIEndpoint https://<your-ai-foundry-endpoint>.openai.azure.com/ `
	-AzureOpenAIDeployment gpt-4o
```

What the script does:
- Ensures Resource Group, App Service Plan (Linux), and Web App exist
- Assigns System Assigned Managed Identity to the Web App
- Configures app settings (Azure OpenAI endpoint, deployment, API version)
- Enables App Service Authentication
- Builds a zip from the repo root (including `templates/` and `static/`) and deploys with `az webapp deploy`

## Deploy with GitHub Actions (CI/CD) – Optional
This repository does not include a workflow by default. You can add one to package the root app and deploy using `az webapp deploy` (or the `azure/webapps-deploy` action).

Recommended GitHub Secrets:
- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (OIDC for Azure login)
- `AZURE_WEBAPP_NAME`, `AZURE_RESOURCE_GROUP`
- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`

Typical workflow steps:
- Checkout, setup Python, install requirements
- Zip the root app (include `templates/` and `static/`)
- Deploy to Azure Web App

## Health and Troubleshooting
- Health probe endpoint: `GET /health` returns `{ "status": "ok" }`
- If you see 401s, ensure App Service Authentication is configured and identity provider is set up
- For Azure OpenAI authorization errors, grant the Web App’s System Assigned Managed Identity the "Cognitive Services User" role on the Azure OpenAI resource
- For local runs, ensure `az login` so `DefaultAzureCredential` can obtain a token

## Security & Privacy
- Set a strong `FLASK_SECRET_KEY`
- App does not persist user data; uploads are deleted immediately after processing
