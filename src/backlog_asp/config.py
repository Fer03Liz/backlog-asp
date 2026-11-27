"""Configuración de la aplicación, leída de variables de entorno o del archivo .env (excluido de Git)."""
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent
# Primero el .env de la carpeta actual y luego el del proyecto. Nunca pisa variables ya definidas.
load_dotenv(find_dotenv(usecwd=True))
load_dotenv(PROJECT_DIR / ".env")

# Cadena de conexión SQLAlchemy, por ejemplo mssql+pymssql://usuario:clave@servidor:1433/base
DATABASE_URL = os.environ.get("SQL_SERVER_STRING_CONNECTION", "")
# Esquema propio dentro de la base: la app no toca objetos de otros esquemas.
DB_SCHEMA = os.environ.get("BACKLOG_DB_SCHEMA", "backlog")
DB_POOL_SIZE = int(os.environ.get("BACKLOG_DB_POOL_SIZE", "5"))

# Motor de la base. "mssql" (SQL Server) es el de siempre y el único del servidor. "sqlite" es solo para probar en
# este equipo sin SQL Server: usa una base local aparte y nunca se conecta a SQL Server (ver PRUEBAS.md).
DB_ENGINE = (os.environ.get("BACKLOG_DB_ENGINE") or "mssql").strip().lower()
if DB_ENGINE not in ("mssql", "sqlite"):
    raise RuntimeError(f"BACKLOG_DB_ENGINE='{DB_ENGINE}' no es válido: use mssql (SQL Server) o sqlite (pruebas).")
_sqlite_path = Path(os.environ.get("BACKLOG_SQLITE_PATH") or "data/pruebas/backlog_pruebas.db")
SQLITE_PATH = _sqlite_path if _sqlite_path.is_absolute() else PROJECT_DIR / _sqlite_path

STATIC_DIR = PACKAGE_DIR / "presentation" / "static"

# Solo escucha en este equipo. Abrirlo a la red requiere aprobación de TI,
# HTTPS y un proxy inverso (ver README, sección "Uso en equipo").
HOST = "127.0.0.1"
PORT = int(os.environ.get("BACKLOG_PORT", "8765"))
# Poner BACKLOG_COOKIE_SECURE=1 cuando la app se publique detrás de HTTPS.
COOKIE_SECURE = os.environ.get("BACKLOG_COOKIE_SECURE") == "1"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SESSION_HOURS = 8          # vida máxima de una sesión
IDLE_MINUTES = 60          # cierre por inactividad
MAX_FAILED_LOGINS = 5      # intentos antes de bloquear el usuario
LOCK_MINUTES = 15
