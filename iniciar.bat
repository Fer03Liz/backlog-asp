@echo off
setlocal
cd /d "%~dp0"
where poetry >nul 2>nul
if errorlevel 1 (
  echo Instalando Poetry...
  py -3 -m pip install --user -q poetry 2>nul || python -m pip install --user -q poetry
)
if not exist .env (
  echo Falta el archivo .env con la conexion a SQL Server. Copie .env.example como .env y complete los datos.
  pause
  exit /b 1
)
echo Revisando dependencias...
poetry install --only main -q
poetry run backlog-admin --si-no-hay
poetry run backlog-asp
pause
