@echo off
setlocal
cd /d "%~dp0"
rem Modo pruebas: base SQLite local en data\pruebas. No se conecta a SQL Server ni abre data\backlog.db.
rem No necesita Poetry: usa el Python de .venv con las versiones de poetry.lock. Ver PRUEBAS.md.
set "BACKLOG_DB_ENGINE=sqlite"
if not defined BACKLOG_PORT set "BACKLOG_PORT=8766"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Creando el entorno .venv...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist "%PY%" (
  echo No se encontro Python 3. Instale Python 3.11 o superior y vuelva a intentarlo.
  pause
  exit /b 1
)
"%PY%" "%~dp0src\backlog_asp\cli\entorno.py"
if errorlevel 1 (
  echo No se pudieron preparar las dependencias. Revise el mensaje de arriba.
  pause
  exit /b 1
)
"%PY%" -m backlog_asp.cli.crear_admin --si-no-hay
"%PY%" -m backlog_asp
pause
