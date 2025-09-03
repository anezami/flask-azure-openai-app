# Text Assistant (Grammar & Translation) — Flask + Azure OpenAI

This is a single-page Flask app that lets users either correct grammar or translate text using Azure OpenAI (GPT-4o). Users authenticate via Google OAuth, and session data (including a simple history preview) is kept only for the current session.

## Features
- Single page with:
  - Text input and file upload (.txt, .md)
  - Checkbox to toggle Translation vs Grammar Check
  - Auto-detect source language (for translation) and prompt for target language
- Azure OpenAI GPT-4o via Azure AI Foundry
- Chunking for large inputs to stay within token limits
- Google OAuth (session-only; no persistence)
- Azure Web App–ready (requirements.txt, startup.cmd)

## Environment Variables
Create a `.env` (for local dev) or configure App Settings in Azure:

```
FLASK_SECRET_KEY=your-random-secret

# Google OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
OAUTH_REDIRECT_URI=https://<your-app>.azurewebsites.net/auth/callback

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<your-ai-foundry-endpoint>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-06-01

# Chunking (optional)
MAX_INPUT_TOKENS=12000
MAX_OUTPUT_TOKENS=2048
TIKTOKEN_ENCODING=o200k_base
AOAI_TEMPERATURE=0.2
```

## Run Locally
1. Create and activate a Python 3.10+ environment.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with the variables above (use http://localhost:8000/auth/callback for redirect locally).
4. Start the app:
   ```
   python app.py
   ```
5. Browse http://localhost:8000

## Deploy to Azure Web App
- Create an Azure Web App (Windows or Linux). For Windows, this repo includes `startup.cmd` and `web.config` as a fallback; for Linux use the startup command:
  ```
  python -m waitress --listen=0.0.0.0:$PORT app:app
  ```
- Configure the environment variables in the Web App's Configuration.
- Deploy the repo (e.g., via GitHub Actions or Zip Deploy). The app entrypoint is `app:app`.

## Notes
- The app stores no persistent user data. Session-only previews are kept in memory and cleared on logout or session end.
- For production, set strong `FLASK_SECRET_KEY` and use HTTPS.
