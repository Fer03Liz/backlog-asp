"""Correo diario por tienda: por despachar (con fecha), en gestión (sin fecha) y despachados.

Identifica cada línea por notificación y número de parte; nunca incluye precios, PO ni datos de otras tiendas.
"""
import io
import json
import zipfile
from collections import defaultdict
from email.message import EmailMessage

from ..domain.calendario import d, fmt, iso
from . import contexto
from .auditoria import audit, get_settings, now_iso


def signature(conn, user) -> str:
    sig = get_settings(conn).get("email_signature", "")
    return f"{user['full_name']}\n{sig}" if user.get("full_name") else sig


def email_groups(conn, ctx, firma: str) -> list:
    t = ctx.today
    transit_days = int(ctx.settings.get("email_transit_days", "0"))
    last_sent = {r["ship_to"]: r["at"][:10] for r in conn.execute(
        "SELECT ship_to, MAX(at) at FROM emails_log GROUP BY ship_to")}
    default_since = iso(ctx.cal.prev_business_before(t))
    horizon = ctx.cal.add_business_days(t, transit_days)

    groups = defaultdict(lambda: {"fecha": [], "gestion": [], "despachados": []})
    for c in contexto.load_cases(conn, ctx, include_closed=True, closed_days=10):
        if c["fecha_despacho"]:
            if c["fecha_despacho"] >= last_sent.get(c["ship_to"], default_since):
                groups[c["ship_to"]]["despachados"].append(c)
        elif c["pendiente"]:
            en_bodega = c["llegada_fuente"] in ("Recibido", "En inventario", "Inventario propio")
            transito = (transit_days > 0 and c["llegada_fuente"] == "KGB (guía)" and c["nivel"] != "rojo"
                        and d(c["llegada"]) <= horizon)
            groups[c["ship_to"]]["fecha" if en_bodega or transito else "gestion"].append(c)

    def ref(c):
        return f"Notificación {c['notifctn']}-{int(c['line_item'] or 0):02d} | Parte {c.get('assembly') or '—'}"

    out = []
    for ship_to, g in groups.items():
        for rows in g.values():
            rows.sort(key=lambda c: (c["notifctn"] or "", c["line_item"] or 0))
        todos = g["fecha"] + g["gestion"] + g["despachados"]
        store = (todos[0]["list_name"] or ship_to).strip()
        parts = [f"Buenos días,\n\nEste es el estado de sus casos de repuesto al {fmt(t)}:"]
        if g["fecha"]:
            parts.append("Por despachar\n" + "\n".join(
                f"- {ref(c)} | Despacho estimado: {fmt(d(c['fecha_cliente']))}" for c in g["fecha"]))
        if g["gestion"]:
            parts.append("En gestión (le informaremos la fecha cuando tengamos el repuesto)\n" + "\n".join(
                f"- {ref(c)}" for c in g["gestion"]))
        if g["despachados"]:
            parts.append("Despachados\n" + "\n".join(
                f"- {ref(c)} | Despachado el {fmt(d(c['fecha_despacho']))}" for c in g["despachados"]))
        parts.append("Saludos,\n" + firma)
        info = ctx.shipto.get(ship_to) or {}
        out.append({"ship_to": ship_to, "tienda": store, "email": info.get("email"), "cc": info.get("cc_email"),
                    "conteo": {k: len(v) for k, v in g.items()},
                    "casos": [c["key_notif"] for c in todos],
                    "fechas": {c["key_notif"]: c["fecha_cliente"] for c in g["fecha"]},
                    "subject": f"Estado de sus casos de repuesto – {store} – {fmt(t)}",
                    "body": "\n\n".join(parts)})
    out.sort(key=lambda g: g["tienda"])
    return out


def eml(group) -> bytes:
    msg = EmailMessage()
    if group["email"]:
        msg["To"] = group["email"]
    if group.get("cc"):
        msg["Cc"] = group["cc"]
    msg["Subject"] = group["subject"]
    msg["X-Unsent"] = "1"   # Outlook lo abre como borrador listo para enviar
    msg.set_content(group["body"])
    return bytes(msg)


def eml_name(group) -> str:
    safe = "".join(ch for ch in group["tienda"] if ch.isalnum() or ch in " -_").strip().replace(" ", "_")[:60]
    return f"correo_{safe or group['ship_to']}.eml"


def registrar(conn, user, group, formato) -> None:
    """Guarda la fecha comunicada (solo de los casos con fecha) y deja el envío en la bitácora."""
    for key, f in group["fechas"].items():
        conn.defer("UPDATE cases SET fecha_comunicada=? WHERE key_notif=?", (f, key))
    conn.defer("INSERT INTO emails_log(at, user_id, ship_to, n_cases, keys) VALUES(?,?,?,?,?)",
               (now_iso(), user["id"], group["ship_to"], len(group["casos"]), json.dumps(group["casos"])))
    audit(conn, user, "email", group["ship_to"], {"casos": len(group["casos"]), "formato": formato})


def zip_del_dia(conn, user, groups) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for g in groups:
            zf.writestr(eml_name(g), eml(g))
            registrar(conn, user, g, "lote")
    return buf.getvalue()
