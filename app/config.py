"""Configuración central. Todo se guarda en la carpeta data/ (excluida de Git)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BACKLOG_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "backlog.db"
BACKUP_DIR = DATA_DIR / "backups"
STATIC_DIR = BASE_DIR / "static"

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
MIN_PASSWORD_LEN = 12
BACKUPS_TO_KEEP = 30

DEFAULT_SETTINGS = {
    "kgb_cust_ids": "460241",          # cliente del KGB (cambiará a Ingram el 2-nov)
    "fr_tolerance_days": "2",          # atendido = despacho hasta ETA + N días
    "fr_tolerance_business": "0",      # 0 = días calendario, 1 = días hábiles (confirmar con la KAM)
    "exchange_codes": "OO",            # garantías que se pagan a precio exchange
    "despachado_grace_days": "3",      # días hábiles tras la ETA antes de avisar que sigue en el reporte
    "kam_alert_days": "2",             # días esperando a la KAM antes de alertar
    "oh_max_days": "1",                # días hábiles máximos de un OH sin despachar
    "currency": "USD",
    "email_signature": "Equipo de Planeación ASP\nIngram Micro",
}

ESTADOS = ["Nuevo", "En gestión", "Cotización enviada", "Pendiente aprobación KAM",
           "Recibido", "Despachado", "Sin solución"]
APROBACION_KAM = ["No requiere", "Pendiente", "Aprobada", "Rechazada"]
PROPUESTAS = ["", "Mantener", "FG", "Upgrade", "Miami", "Sustituto", "Otra"]
ROLES = ["admin", "editor", "lector"]
