:: Deprecated: This project targets Linux App Service using gunicorn. This file is no longer used.
@echo off
echo startup.cmd is deprecated and unused.
exit /b 0
@echo off
REM Azure App Service provides PORT env var
if "%PORT%"=="" set PORT=8000
python -m waitress --listen=0.0.0.0:%PORT% app:app
