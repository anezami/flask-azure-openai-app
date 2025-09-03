# Text Assistant (Grammar & Translation) — Flask + Azure OpenAI

Single-page Flask app for grammar correction and translation powered by Azure OpenAI (GPT-4o), with Google OAuth login, file upload, chunking for large texts, and UI localization (English/German).

## Features
- Single-page UI with:
  - Text input and file upload (`.txt`, `.md`, `.docx`, `.pdf`)
  - Mode selection dropdown: `Grammar Check` (default) or `Translation`
  - Auto-detect source language; when in Translation mode, choose a target language
  - Language selector (UI locale) on the top-right: English/German
- Azure OpenAI GPT-4o via the official `openai` SDK (Azure endpoint)
- Chunking for large inputs to respect token budgets
- Google OAuth with optional email allowlist
- Azure App Service–ready (`requirements.txt`, `startup.cmd`, `web.config`)

Notes:
- Preview and session-history UI have been removed for a cleaner experience. The app does not persist data; uploaded files are deleted immediately after processing.

## Environment Variables
Create a `.env` for local development or configure App Settings in Azure. Relevant variables:

```
# Flask
FLASK_SECRET_KEY=your-random-secret
SESSION_COOKIE_SECURE=false           # true in production
SESSION_COOKIE_SAMESITE=Lax

# Google OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
OAUTH_REDIRECT_URI=https://<your-app>.azurewebsites.net/auth/callback
ALLOWED_GOOGLE_EMAILS=alice@contoso.com,bob@contoso.com   # optional allowlist (comma-separated). If empty, allow all.
DISABLE_AUTH=false                   # set to true to bypass login locally

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<your-ai-foundry-endpoint>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-06-01

# Model/runtime tuning (optional)
AOAI_TEMPERATURE=0.2
AOAI_HTTP_TIMEOUT=60
MAX_INPUT_TOKENS=12000
MAX_OUTPUT_TOKENS=2048
TIKTOKEN_ENCODING=o200k_base

# UI
UI_LANG=en  # default UI language if session not set (en|de)
```

## Run Locally (Windows PowerShell)
```pwsh
# From project root
python -m venv .venv
./.venv/Scripts/Activate.ps1
pip install -r requirements.txt

# Create .env (see the Environment Variables section). For local OAuth callback, use:
# OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback

# Optionally bypass Google login during local dev
# set in .env: DISABLE_AUTH=true

python app.py
```
Browse `http://localhost:8000`.

## Google OAuth Setup (summary)
1. In Google Cloud Console, create OAuth 2.0 credentials (Web application).
2. Authorized redirect URI (local): `http://localhost:8000/auth/callback`
3. Authorized redirect URI (prod): `https://<your-app>.azurewebsites.net/auth/callback`
4. Put `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `OAUTH_REDIRECT_URI` into your environment.
5. Optionally set `ALLOWED_GOOGLE_EMAILS` to restrict access.

## Deploy to Azure App Service
- Create an App Service (Windows or Linux). This repo includes `startup.cmd` and `web.config` for Windows deployments.
- Configure the environment variables from the section above.
- For Linux, a typical startup command is:
  ```bash
  python -m waitress --listen=0.0.0.0:$PORT app:app
  ```
- Deploy via GitHub Actions or Zip Deploy. The WSGI entrypoint is `app:app`.
- Health probe endpoint: `GET /health` returns `{"status":"ok"}`.

## Security & Privacy
- Strongly set `FLASK_SECRET_KEY` and enable `SESSION_COOKIE_SECURE=true` behind HTTPS.
- The app stores no persistent user data. Temporary uploads are deleted after processing.
- Use `ALLOWED_GOOGLE_EMAILS` to restrict access by email domain/account.

## Troubleshooting
- OAuth callback issues: ensure `OAUTH_REDIRECT_URI` matches exactly in Google Console and environment.
- Azure OpenAI errors: verify `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_DEPLOYMENT`.
- Large files: tune `MAX_INPUT_TOKENS`, `MAX_OUTPUT_TOKENS`, and `TIKTOKEN_ENCODING`.
