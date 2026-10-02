@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo Creando entorno virtual...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Revisando dependencias...
python -m pip install -q --disable-pip-version-check -r requirements.txt
python scripts\crear_admin.py --si-no-hay
python run.py
pause
