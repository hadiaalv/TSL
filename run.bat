@echo off
cd /d "%~dp0"
if not exist .venv (
  py -3.14 -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
.venv\Scripts\python.exe -c "import pypdf, openpyxl" >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
if not exist .env (
  copy .env.example .env
  echo Created .env — add your GROK_API_KEY then re-run.
  pause
  exit /b 1
)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
