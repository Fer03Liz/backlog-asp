"""Bitácora de solo agregar y parámetros guardados en la base."""
import json
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def audit(conn, user, action, target=None, detail=None) -> None:
    """Registra una acción. La tabla tiene triggers que impiden editar o borrar."""
    conn.defer(
        "INSERT INTO audit_log(at, user_id, username, action, target, detail) VALUES(?,?,?,?,?,?)",
        (now_iso(), user["id"] if user else None, user["username"] if user else None, action,
         str(target)[:400] if target is not None else None,
         json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None),
    )


def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT [key], value FROM settings")}
