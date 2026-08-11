@echo off
REM iDev.Gen - starts the backend and opens the built UI at http://127.0.0.1:8777
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtualenv...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r backend\requirements.txt
)
if not exist "frontend\dist\index.html" (
  echo Building frontend...
  pushd frontend && npm install && npm run build && popd
)
start "" http://127.0.0.1:8777
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8777
