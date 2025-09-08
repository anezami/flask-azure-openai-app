# Grammar & Translation Assistant (Flask + Azure OpenAI)

## High-Level Overview

This application is a production-oriented grammar correction and translation assistant built with Flask and Azure OpenAI (GPT‑4o). Users submit large bodies of text for either grammar enhancement or translation. The text is:

1. Token-chunked safely (large inputs handled efficiently)
2. Processed in parallel with retry, backoff, and circuit breaker logic
3. Streamed back to the browser via **Server-Sent Events (SSE)** with a live progress bar
4. Sanitized to ensure the model returns only the corrected or translated text (no labels/filler)
5. Logged with structured JSON metrics (file-based + optional Application Insights / OpenTelemetry)

The app relies on Azure App Service built-in authentication (e.g., Google / Entra ID) and uses a **System Assigned Managed Identity** to call Azure OpenAI—no API keys required. All uploads are processed in-memory (temporary file only) and then deleted; no persistent storage of user text.

---
## Architecture At a Glance

Component | Responsibility
--------- | --------------
`app.py` | Flask routes, async job orchestration, SSE endpoint, retry/circuit logic
`azure_openai_client.py` | Thin wrapper invoking Azure OpenAI via official SDK using Managed Identity
`chunking.py` | Token-based safe splitting of large inputs
`i18n.py` | UI localization (English / German)
`templates/index.html` | Single-page UI (progress bar, jump navigation, SSE integration)
`tests/` | Pytest unit tests (sanitizer, async retry & circuit breaker)
`infra/` | Bicep template for Azure resource provisioning + deploy script

Key runtime flow:
1. User submits form → `/process` starts async job (thread) and returns page with `job_id`.
2. Browser opens `/job/<id>/stream` (SSE) receiving events: `started`, `progress`, `error`, `final`.
3. Backend processes chunks concurrently (`ThreadPoolExecutor`).
4. Each chunk uses filtered retry (429 & 5xx) with exponential backoff + jitter.
5. Circuit breaker halts further processing after N consecutive chunk failures.
6. Structured metrics appended to `metrics.log` and optionally exported to Application Insights.
7. Final result inserted into DOM dynamically.

---
## Resilience & Observability

Feature | Details
------- | -------
Retry Filtering | Only 429 + 5xx (or textual rate-limit hints) trigger retry
Backoff | Exponential with base + jitter (env configurable)
Circuit Breaker | Aborts after configurable consecutive chunk failures
Structured Metrics | JSON lines persisted to `metrics.log`
App Insights (Optional) | Enable via `APPLICATIONINSIGHTS_CONNECTION_STRING`
SSE Streaming | Live progress events (fallback to polling if EventSource fails)
Sanitization | Removes code fences / labels from model output

---
## Deploy to Azure Web App (with Google Auth)

Infrastructure as Code (Bicep) + PowerShell script provision:
* Linux Web App (App Service Plan)
* App Service Authentication with Google (Easy Auth v2)
* Key Vault (stores Google secret, optional future secrets)
* Managed Identity with RBAC on Azure OpenAI

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

---
## Core Features

1. Grammar correction or translation mode
2. Large text handling via token chunking (configurable token budget)
3. Parallel chunk processing with order preservation
4. Intelligent retry & circuit breaker
5. Live SSE progress bar & jump navigation
6. Copy-to-clipboard output
7. UI localization (EN/DE)
8. File uploads: `.txt`, `.md`, `.docx`, `.pdf`
9. Structured metrics + optional telemetry
10. Auth allowlist via `ALLOWED_EMAILS`

## Prerequisites
- Azure subscription with permission to create Resource Groups, App Service Plans, and Web Apps
- Azure CLI installed and logged in (`az login`)
- GitHub account and repository (for CI/CD)
- An Azure OpenAI resource and a model deployment name (e.g., `gpt-4o`)

## Environment Variables
Create a `.env` or set Azure App Settings:

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

# Async & Concurrency
MAX_PARALLEL_REQUESTS=4
RETRY_MAX_ATTEMPTS=3
RETRY_BASE_DELAY_SECS=1.0
RETRY_BACKOFF_FACTOR=2.0
RETRY_JITTER_SECS=0.25
RETRY_STATUS_CODES=429,500,502,503,504
CIRCUIT_BREAKER_FAILURE_THRESHOLD=3

# Metrics / Telemetry
METRICS_FILE_PATH=metrics.log
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=...
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

## Testing

Run unit tests (sanitization, retries, circuit breaker, async job):
```pwsh
pytest -q
```

## Security & Privacy
- Set a strong `FLASK_SECRET_KEY`
- App does not persist user text; uploads are deleted immediately post-read
- Use `ALLOWED_EMAILS` to restrict access beyond IdP
- Consider enabling HTTPS-only and setting `COOKIE_SECURE` in production

## Roadmap / Ideas
- WebSockets option (currently SSE + fallback polling)
- Token usage reporting per chunk
- Persistent job store (Redis) for multi-instance scaling
- Fine-grained role-based features

## License
MIT (adjust as required)
