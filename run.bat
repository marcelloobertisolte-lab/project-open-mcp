@echo off
REM Avvia il server MCP Project-Open in modalita stdio.
REM Richiede di aver gia installato il pacchetto: pip install -e .
REM
REM Variabili lette da .env (PO_BASE_URL, PO_USERNAME, PO_PASSWORD, ...).

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python -m project_open_mcp %*
