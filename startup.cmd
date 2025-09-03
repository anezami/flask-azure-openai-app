@echo off
REM Azure App Service provides PORT env var
if "%PORT%"=="" set PORT=8000
python -m waitress --listen=0.0.0.0:%PORT% app:app
