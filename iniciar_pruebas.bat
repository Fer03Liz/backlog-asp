@echo off
setlocal
cd /d "%~dp0"
rem Modo pruebas: base SQLite local en data\pruebas. No se conecta a SQL Server ni abre data\backlog.db.
rem Estas variables mandan sobre el .env (si existe). Ver PRUEBAS.md.
set "BACKLOG_DB_ENGINE=sqlite"
if not defined BACKLOG_PORT set "BACKLOG_PORT=8766"
where poetry >nul 2>nul
if errorlevel 1 (
  echo Instalando Poetry...
  py -3 -m pip install --user -q poetry 2>nul || python -m pip install --user -q poetry
)
echo Revisando dependencias...
poetry install --only main -q
poetry run backlog-admin --si-no-hay
poetry run backlog-asp
pause
