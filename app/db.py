"""Acceso a SQLite: esquema, bitácora de solo agregar y respaldos."""
import json
import sqlite3
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  full_name TEXT,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','editor','lector')),
  active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS loads(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  file_name TEXT,
  file_hash TEXT NOT NULL,
  file_date TEXT,
  loaded_at TEXT NOT NULL,
  user_id INTEGER,
  rows INTEGER,
  summary TEXT,
  UNIQUE(kind, file_hash)
);
CREATE TABLE IF NOT EXISTS cases(
  key_notif TEXT PRIMARY KEY,
  notifctn TEXT, line_item INTEGER, assembly TEXT, description TEXT,
  created_on TEXT, po_number TEXT, ship_to TEXT, list_name TEXT, material_desc TEXT,
  serial_no TEXT, warranty TEXT, report_date TEXT, product_family TEXT,
  repair_strategy TEXT, product_description TEXT,
  -- Campos de la KAM de Apple (solo lectura en la app)
  option_jenny TEXT, eta_jenny TEXT, eta_first TEXT, eta_changes INTEGER NOT NULL DEFAULT 0,
  comments_jenny TEXT, notes_jenny TEXT, tracking TEXT, subs TEXT,
  -- Ciclo de vida según el archivo diario
  first_seen TEXT, last_seen TEXT, source_status TEXT NOT NULL DEFAULT 'abierto',
  closed_on TEXT, close_reason TEXT, reopened INTEGER NOT NULL DEFAULT 0, oh_since TEXT,
  -- Gestión del equipo Ingram
  estado TEXT NOT NULL DEFAULT 'Nuevo', responsable TEXT,
  fecha_recepcion TEXT, fecha_despacho TEXT, fecha_objetivo TEXT, fecha_comunicada TEXT,
  propuesta TEXT, aprobacion_kam TEXT NOT NULL DEFAULT 'No requiere',
  kam_solicitado TEXT, kam_respuesta TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_ship ON cases(ship_to);
CREATE TABLE IF NOT EXISTS case_history(
  id INTEGER PRIMARY KEY, key_notif TEXT NOT NULL, load_id INTEGER, file_date TEXT,
  field TEXT NOT NULL, old TEXT, new TEXT
);
CREATE INDEX IF NOT EXISTS idx_hist_key ON case_history(key_notif);
CREATE TABLE IF NOT EXISTS snapshots(
  file_date TEXT NOT NULL, key_notif TEXT NOT NULL, option_jenny TEXT, eta_jenny TEXT,
  PRIMARY KEY(file_date, key_notif)
);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, key_notif TEXT NOT NULL, user_id INTEGER, username TEXT,
  at TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_key ON comments(key_notif);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, username TEXT,
  action TEXT NOT NULL, target TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'La bitácora es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'La bitácora es de solo lectura'); END;
CREATE TRIGGER IF NOT EXISTS comments_no_update BEFORE UPDATE ON comments
  BEGIN SELECT RAISE(ABORT, 'Los comentarios no se editan'); END;
CREATE TRIGGER IF NOT EXISTS comments_no_delete BEFORE DELETE ON comments
  BEGIN SELECT RAISE(ABORT, 'Los comentarios no se borran'); END;
CREATE TABLE IF NOT EXISTS kgb(
  tracking TEXT, delivery_id TEXT, delivery_item TEXT, poso TEXT, prod_id TEXT, prod_norm TEXT,
  prod_desc TEXT, status TEXT, current_event TEXT, event_dt TEXT, edd TEXT, pgi_dt TEXT,
  sla_ind TEXT, issue TEXT, qty REAL, cust_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_kgb_trk ON kgb(tracking);
CREATE INDEX IF NOT EXISTS idx_kgb_prod ON kgb(prod_norm);
CREATE TABLE IF NOT EXISTS prices(
  part TEXT PRIMARY KEY, part_norm TEXT, description TEXT,
  stocking REAL, exchange REAL, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prices_norm ON prices(part_norm);
CREATE TABLE IF NOT EXISTS shipto(
  ship_to TEXT PRIMARY KEY, name TEXT, email TEXT, city TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS guide_arrivals(
  tracking TEXT PRIMARY KEY, arrived_on TEXT NOT NULL, user_id INTEGER, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holidays_extra(day TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS emails_log(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, ship_to TEXT,
  n_cases INTEGER, keys TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Una conexión por petición; FastAPI puede usarla desde otro hilo del pool.
    conn = sqlite3.connect(config.DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for k, v in config.DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        # v2: la KAM ahora se llama Apple Care Manager (ACM)
        conn.execute("UPDATE cases SET estado='Pendiente aprobación ACM' WHERE estado='Pendiente aprobación KAM'")
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def audit(conn, user, action, target=None, detail=None) -> None:
    """Registra una acción. La tabla tiene triggers que impiden editar o borrar."""
    conn.execute(
        "INSERT INTO audit_log(at, user_id, username, action, target, detail) VALUES(?,?,?,?,?,?)",
        (now_iso(), user["id"] if user else None, user["username"] if user else None, action, target,
         json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None),
    )


def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def backup(tag: str = "") -> str:
    """Copia consistente de la base (API de respaldo de SQLite) con rotación."""
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"backlog_{datetime.now():%Y%m%d_%H%M%S}{'_' + tag if tag else ''}.db"
    src = connect()
    dst = sqlite3.connect(config.BACKUP_DIR / name)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(config.BACKUP_DIR.glob("backlog_*.db"))[:-config.BACKUPS_TO_KEEP]:
        old.unlink(missing_ok=True)
    return name
