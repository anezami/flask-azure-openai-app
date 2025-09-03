# ChatGPTKZ2

## Deploy to Azure Web App (with Google Auth)

This repo includes infrastructure as code (Bicep) and a PowerShell script to provision and deploy an Azure Linux Web App with built-in authentication configured for Google, and an Azure Key Vault to store Google credentials. The script also grants the web app’s system-managed identity access to your Azure OpenAI resource.

### Prerequisites
- Azure CLI installed and logged in: `az login`
- Subscription ID
- A Google OAuth Client (Client ID and Client Secret).
	- Authorized redirect URI: `https://<app-name>.azurewebsites.net/.auth/login/google/callback`

### One-time: Provision infra + deploy code

1) Customize `infra/azuredeploy.parameters.json` with your values or pass them via the script.

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

Re-running the script is safe (idempotent). It uses ARM/Bicep deployment and Zip Deploy for the app code.

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
Configure Authentication in the Azure Web App (Portal > Your Web App > Authentication):
- Add an Identity Provider (e.g., Google) and set action to "Log in with..."; unauthenticated action: "Redirect to login page".
- The app reads user info from `X-MS-CLIENT-PRINCIPAL` headers injected by App Service.

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
- Builds a zip from the repo root (excluding `.git`, `.venv`) and deploys

## Deploy with GitHub Actions (CI/CD)
This repo includes `.github/workflows/deploy.yml` to build and deploy on push to `master`.

Set the following GitHub Secrets in your repository:
- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (for Azure login via OIDC)
- `AZURE_WEBAPP_NAME`, `AZURE_RESOURCE_GROUP`
- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`

Workflow summary:
- Checks out code, sets up Python, installs root requirements
- Zips the root app (excludes `.git`, `.venv`) and deploys with `azure/webapps-deploy`
- Ensures app settings are configured prior to deploy

## Health and Troubleshooting
- Health probe endpoint: `GET /health` returns `{ "status": "ok" }`
- If you see 401s, ensure App Service Authentication is configured and identity provider is set up
- For Azure OpenAI authorization errors, grant the Web App’s System Assigned Managed Identity the "Cognitive Services User" role on the Azure OpenAI resource
- For local runs, ensure `az login` so `DefaultAzureCredential` can obtain a token

## Security & Privacy
- Set a strong `FLASK_SECRET_KEY`
- App does not persist user data; uploads are deleted immediately after processing
