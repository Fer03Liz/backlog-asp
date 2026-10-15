"""Bloque B3: compras en Miami registradas a mano en cada caso.

Mientras el Open Orders no traiga el campo de Miami, el equipo registra aquí cada compra (fecha, cantidad, PO).
Con eso el tablero usa la llegada de Miami como fecha del caso y avisa cuando Apple trae la parte antes,
para evaluar cancelar la compra. Cuando llegue el campo en el Open Orders, se enlaza con oo_sales_ord/oo_item.
"""
from datetime import date, timedelta

from . import db, logic

SCHEMA_MIAMI = """
CREATE TABLE IF NOT EXISTS miami_purchases(
  id INTEGER PRIMARY KEY, key_notif TEXT NOT NULL, part TEXT, part_norm TEXT, cantidad REAL NOT NULL DEFAULT 1,
  fecha_compra TEXT NOT NULL, eta TEXT, po TEXT, notas TEXT,
  estado TEXT NOT NULL DEFAULT 'Comprada' CHECK(estado IN ('Comprada','Recibida','Cancelada')),
  fecha_estado TEXT, motivo TEXT, oo_sales_ord TEXT, oo_item TEXT, origen TEXT NOT NULL DEFAULT 'manual',
  user_id INTEGER, username TEXT, created_at TEXT NOT NULL, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_miami_key ON miami_purchases(key_notif);
CREATE INDEX IF NOT EXISTS idx_miami_part ON miami_purchases(part_norm);
"""


class MiamiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def init_db() -> None:
    conn = db.connect()
    try:
        conn.executescript(SCHEMA_MIAMI)
        conn.commit()
    finally:
        conn.close()


def _fecha(valor, campo, obligatoria=False):
    if valor in (None, ""):
        if obligatoria:
            raise MiamiError(f"Indique {campo}.", 422)
        return None
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise MiamiError(f"Fecha no válida en {campo}.", 422)


# ---------------------------------------------------------------- contexto para enrich
def cargar_contexto(ctx, conn) -> None:
    """Se llama al final de Context.__init__: compras activas por caso y llegada estimada de Open Orders por parte."""
    ctx.miami_lead = int(ctx.settings.get("miami_lead_days", "7"))
    ctx.miami_alertas = alertas
    ctx.miami, ctx.oo_llegada = {}, {}
    try:
        for r in conn.execute("SELECT * FROM miami_purchases WHERE estado='Comprada' ORDER BY id"):
            ctx.miami[r["key_notif"]] = dict(r)
        for r in conn.execute("SELECT part_norm, MIN(llegada_est) m FROM open_orders WHERE llegada_est IS NOT NULL GROUP BY part_norm"):
            ctx.oo_llegada[r["part_norm"]] = r["m"]
    except Exception:          # sin tabla de Open Orders todavía
        pass


def alertas(c, ctx, compra, limite, pnorm, sustituto) -> list:
    """Alertas de Miami para un caso pendiente."""
    t, cal, out = ctx.today, ctx.cal, []
    opt = c.get("option_jenny") or ""
    if compra:
        eta = logic.d(compra.get("eta"))
        grupo = {pnorm} | ({logic.norm_part(sustituto)} if sustituto else set())
        kgb_edd = min((ctx.kgb_by_part[p]["edd"] for p in grupo if p in ctx.kgb_by_part and ctx.kgb_by_part[p]["edd"]), default=None)
        oo_lleg = min((ctx.oo_llegada[p] for p in grupo if p in ctx.oo_llegada), default=None)
        for fuente, f in (("el KGB (con guía)", logic.d(kgb_edd)), ("un pedido en Open Orders", logic.d(oo_lleg))):
            if not f:
                continue
            despacho = max(cal.next_business_after(f), cal.first_business_from(t))
            if limite and despacho <= limite:
                motivo = f"alcanza a despacharse dentro del límite ({logic.fmt(limite)})"
            elif eta and f < eta:
                motivo = f"llega antes que Miami ({logic.fmt(eta)})"
            else:
                continue
            out.append({"code": "MIAMI_EVALUAR", "level": "ambar",
                        "text": f"Apple trae esta parte por {fuente} hacia el {logic.fmt(f)} y {motivo}. "
                                "Evaluar cancelar la compra en Miami si esa unidad no está asignada a otro caso."})
            break
        if eta and eta < t:
            out.append({"code": "MIAMI_ATRASADA", "level": "ambar",
                        "text": f"La compra en Miami debía llegar el {logic.fmt(eta)} y no está marcada como recibida."})
    elif opt == "Miami":
        out.append({"code": "MIAMI_SIN_COMPRA", "level": "info", "text": "Solución Miami sin compra registrada en el tablero."})
    return out


# ---------------------------------------------------------------- registro
def listar_caso(conn, key) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM miami_purchases WHERE key_notif=? ORDER BY id DESC", (key,))]


def registrar(conn, key, body: dict, user) -> dict:
    case = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not case:
        raise MiamiError("Caso no encontrado.", 404)
    if conn.execute("SELECT 1 FROM miami_purchases WHERE key_notif=? AND estado='Comprada'", (key,)).fetchone():
        raise MiamiError("Este caso ya tiene una compra en Miami activa. Márquela recibida o cancélela primero.", 409)
    fecha = _fecha(body.get("fecha_compra"), "la fecha de compra", obligatoria=True)
    eta = _fecha(body.get("eta"), "la llegada esperada")
    if not eta:
        lead = int(db.get_settings(conn).get("miami_lead_days", "7"))
        eta = (date.fromisoformat(fecha) + timedelta(days=lead)).isoformat()
    if eta < fecha:
        raise MiamiError("La llegada esperada no puede ser anterior a la compra.", 422)
    try:
        cantidad = float(body.get("cantidad") or 1)
    except (TypeError, ValueError):
        raise MiamiError("Cantidad no válida.", 422)
    if not 0 < cantidad <= 999:
        raise MiamiError("La cantidad debe estar entre 1 y 999.", 422)
    part = (body.get("part") or case["assembly"] or "").strip().upper()[:40]
    po = (body.get("po") or "").strip()[:60] or None
    notas = (body.get("notas") or "").strip()[:500] or None
    now = db.now_iso()
    cur = conn.execute(
        """INSERT INTO miami_purchases(key_notif, part, part_norm, cantidad, fecha_compra, eta, po, notas, estado,
           user_id, username, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?, 'Comprada', ?,?,?,?)""",
        (key, part, logic.norm_part(part), cantidad, fecha, eta, po, notas, user["id"], user["username"], now, now))
    db.audit(conn, user, "miami_compra", key, {"id": cur.lastrowid, "parte": part, "cantidad": cantidad,
                                                "fecha_compra": fecha, "eta": eta, "po": po})
    return {"id": cur.lastrowid, "eta": eta}


def actualizar(conn, pid: int, body: dict, user) -> dict:
    row = conn.execute("SELECT * FROM miami_purchases WHERE id=?", (pid,)).fetchone()
    if not row:
        raise MiamiError("Compra no encontrada.", 404)
    old = dict(row)
    accion = body.get("accion")
    hoy = logic.today().isoformat()
    now = db.now_iso()
    if accion in ("recibida", "cancelada") and old["estado"] != "Comprada":
        raise MiamiError(f"La compra ya está {old['estado'].lower()}.", 409)
    if accion == "recibida":
        fecha = _fecha(body.get("fecha"), "la fecha de recepción") or hoy
        conn.execute("UPDATE miami_purchases SET estado='Recibida', fecha_estado=?, updated_at=? WHERE id=?", (fecha, now, pid))
        # Igual que la llegada de una guía: marca la recepción del caso si no la tenía.
        case = dict(conn.execute("SELECT * FROM cases WHERE key_notif=?", (old["key_notif"],)).fetchone())
        diff = {}
        if not case["fecha_recepcion"]:
            diff["fecha_recepcion"] = {"antes": None, "ahora": fecha}
        if case["estado"] in ("Nuevo", "En gestión"):
            diff["estado"] = {"antes": case["estado"], "ahora": "Recibido"}
        if diff:
            sets = {k: v["ahora"] for k, v in diff.items()}
            conn.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE key_notif=?",
                         [*sets.values(), now, old["key_notif"]])
            db.audit(conn, user, "case_update", old["key_notif"], {**diff, "origen": f"compra en Miami recibida (#{pid})"})
        detalle = {"estado": "Recibida", "fecha": fecha}
    elif accion == "cancelada":
        motivo = (body.get("motivo") or "").strip()[:300]
        if not motivo:
            raise MiamiError("Indique el motivo de la cancelación.", 422)
        conn.execute("UPDATE miami_purchases SET estado='Cancelada', fecha_estado=?, motivo=?, updated_at=? WHERE id=?",
                     (hoy, motivo, now, pid))
        detalle = {"estado": "Cancelada", "motivo": motivo}
    elif accion == "editar":
        cambios = {}
        if "eta" in body:
            cambios["eta"] = _fecha(body.get("eta"), "la llegada esperada", obligatoria=True)
        if "po" in body:
            cambios["po"] = (body.get("po") or "").strip()[:60] or None
        if "notas" in body:
            cambios["notas"] = (body.get("notas") or "").strip()[:500] or None
        cambios = {k: v for k, v in cambios.items() if v != old[k]}
        if not cambios:
            return {"ok": True, "cambios": 0}
        conn.execute(f"UPDATE miami_purchases SET {', '.join(f'{k}=?' for k in cambios)}, updated_at=? WHERE id=?",
                     [*cambios.values(), now, pid])
        detalle = {k: {"antes": old[k], "ahora": v} for k, v in cambios.items()}
    else:
        raise MiamiError("Acción no válida.", 422)
    db.audit(conn, user, f"miami_{accion}", old["key_notif"], {"id": pid, **detalle})
    return {"ok": True}


def listado(conn, ctx, incluir_cerradas: bool = False) -> dict:
    """Todas las compras en Miami con el estado del caso y sus alertas (para la pestaña Abastecimiento)."""
    sql = "SELECT * FROM miami_purchases" + ("" if incluir_cerradas else " WHERE estado='Comprada'") + " ORDER BY COALESCE(eta,'9999'), id"
    compras = [dict(r) for r in conn.execute(sql)]
    keys = {c["key_notif"] for c in compras}
    casos = {}
    if keys:
        marcas = ",".join("?" * len(keys))
        for r in conn.execute(f"SELECT * FROM cases WHERE key_notif IN ({marcas})", list(keys)):
            casos[r["key_notif"]] = logic.enrich(dict(r), ctx)
    for c in compras:
        caso = casos.get(c["key_notif"]) or {}
        c["tienda"] = caso.get("list_name")
        c["opcion"] = caso.get("option_short")
        c["limite_fr"] = caso.get("limite_fr")
        c["caso_abierto"] = caso.get("source_status") == "abierto"
        c["alertas"] = [a for a in caso.get("alerts", []) if a["code"].startswith("MIAMI_")] if c["estado"] == "Comprada" else []
    sin_compra = sum(1 for r in conn.execute(
        "SELECT key_notif FROM cases WHERE source_status='abierto' AND fecha_despacho IS NULL AND option_jenny='Miami'")
        if r["key_notif"] not in ctx.miami)
    return {"compras": compras, "lead_dias": ctx.miami_lead,
            "resumen": {"activas": sum(1 for c in compras if c["estado"] == "Comprada"),
                        "evaluar": sum(1 for c in compras if any(a["code"] == "MIAMI_EVALUAR" for a in c["alertas"])),
                        "atrasadas": sum(1 for c in compras if any(a["code"] == "MIAMI_ATRASADA" for a in c["alertas"])),
                        "miami_sin_compra": sin_compra}}
