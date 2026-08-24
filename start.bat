@echo off
setlocal
REM iDev.Gen - starts the backend and opens the built UI.
REM Optional first argument: the host to bind. Defaults to 127.0.0.1
REM (loopback only). Accepted values are 127.0.0.1, localhost and 0.0.0.0;
REM anything else falls back to loopback. Reaching the app from the network
REM is opt-in, so a stray or mistyped argument cannot expose it by accident.
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

set "HOST=127.0.0.1"
set "WARN="
if /I "%~1"=="0.0.0.0" set "HOST=0.0.0.0" & goto :serve
if /I "%~1"=="127.0.0.1" goto :serve
if /I "%~1"=="localhost" goto :serve
if "%~1"=="" goto :serve
set "WARN=1"

:serve
if defined WARN (
  echo Unrecognised host "%~1" - falling back to loopback ^(127.0.0.1^).
  echo Run start-lan.bat to expose the app to the network.
)
REM The browser is always pointed at loopback, whatever the bind address is:
REM 0.0.0.0 is an address to listen on, not one to connect to, and a browser
REM sent there fails while the server is running perfectly - the most confusing
REM possible greeting at the moment start-lan.bat is first tried.
if "%HOST%"=="0.0.0.0" (
  echo Serving on every interface, port 8777.
  echo On this machine:   http://127.0.0.1:8777
  echo From another device: http://^<this machine's address on the network^>:8777
) else (
  echo Serving on http://%HOST%:8777
)
start "" http://127.0.0.1:8777
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host %HOST% --port 8777
endlocal
