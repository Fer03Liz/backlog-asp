"""Parámetros, días no hábiles, correos de tiendas, historial de cargas y bitácora."""
import json

from ..domain.calendario import holidays_co, today
from ..domain.catalogos import DEFAULT_SETTINGS, SETTINGS_ENTEROS
from ..domain.errores import ErrorNegocio
from .auditoria import audit, get_settings, now_iso


def parametros(conn) -> dict:
    settings, extra = conn.select_many([("SELECT [key], value FROM settings", ()),
                                        ("SELECT day, name FROM holidays_extra ORDER BY day", ())])
    hoy = today()
    return {"settings": {r["key"]: r["value"] for r in settings},
            "festivos_extra": [dict(r) for r in extra],
            "festivos": sorted(x.isoformat() for y in (hoy.year, hoy.year + 1) for x in holidays_co(y) if x >= hoy)}


def guardar_parametros(conn, user, valores: dict) -> None:
    for k, v in valores.items():
        if k not in DEFAULT_SETTINGS:
            raise ErrorNegocio(f"Parámetro desconocido: {k}", 422)
        if k in SETTINGS_ENTEROS and not (v.isdigit() and 0 <= int(v) <= 30):
            raise ErrorNegocio(f"{k} debe ser un número entre 0 y 30.", 422)
        if k == "fr_tolerance_business" and v not in ("0", "1"):
            raise ErrorNegocio("fr_tolerance_business debe ser 0 o 1.", 422)
        if len(v) > 500:
            raise ErrorNegocio(f"{k} es demasiado largo.", 422)
    old = get_settings(conn)
    for k, v in valores.items():
        conn.upsert("settings", {"key": k, "value": v.strip()}, ("key",), defer=True)
    audit(conn, user, "settings", None, {k: {"antes": old.get(k), "ahora": v} for k, v in valores.items() if old.get(k) != v})


def agregar_festivo(conn, user, day: str, name: str) -> None:
    conn.upsert("holidays_extra", {"day": day, "name": name.strip()}, ("day",), defer=True)
    audit(conn, user, "holiday_add", day, {"nombre": name})


def quitar_festivo(conn, user, day: str) -> None:
    conn.defer("DELETE FROM holidays_extra WHERE day=?", (day,))
    audit(conn, user, "holiday_delete", day)


def bitacora(conn, limit: int) -> list:
    limit = max(1, min(limit, 2000))
    return [dict(r) for r in conn.execute(
        "SELECT TOP (?) at, username, action, target, detail FROM audit_log ORDER BY id DESC", (limit,))]


def cargas(conn) -> list:
    return [{**dict(r), "summary": json.loads(r["summary"] or "{}")} for r in conn.execute(
        """SELECT TOP 60 l.kind, l.file_name, l.file_date, l.loaded_at, l.[rows] AS [rows], l.summary, u.username
           FROM loads l LEFT JOIN users u ON u.id = l.user_id ORDER BY l.id DESC""")]


def guardar_tienda(conn, user, ship_to: str, email: str | None, city: str | None) -> None:
    """Cambia el correo (o la ciudad) de una tienda sin borrar la copia (CC) ni el cliente."""
    email = (email or "").strip() or None
    if email and not all("@" in e and "." in e.split("@")[-1] for e in email.replace(",", ";").split(";") if e.strip()):
        raise ErrorNegocio("Correo no válido. Separe varios correos con punto y coma.", 422)
    actual, caso = conn.select_many([("SELECT * FROM shipto WHERE ship_to=?", (ship_to,)),
                                     ("SELECT TOP 1 list_name FROM cases WHERE ship_to=?", (ship_to,))])
    old = dict(actual[0]) if actual else {}
    name = old.get("name") or (caso[0]["list_name"] if caso else None)
    city = (city or "").strip() or old.get("city")
    primero = (email or "").replace(",", ";").split(";")[0].strip()
    cliente = primero.split("@")[-1].lower() if "@" in primero else old.get("cliente")
    conn.upsert("shipto", {"ship_to": ship_to, "name": name, "email": email, "cc_email": old.get("cc_email"),
                           "cliente": cliente, "city": city, "updated_at": now_iso()}, ("ship_to",), defer=True)
    audit(conn, user, "shipto_update", ship_to, {"email": {"antes": old.get("email"), "ahora": email}})
