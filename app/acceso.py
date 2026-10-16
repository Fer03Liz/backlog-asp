"""Roles externos: Apple Care Manager (acm) y tienda.

- admin, editor y lector son del equipo Ingram y ven todo (lector sin editar).
- acm (Jenny): ve todos los casos, Abastecimiento e Indicadores. No edita, pero deja notas que ve el equipo.
  No ve precios, compras en Miami, guías, correos, cargas ni administración. De los comentarios del equipo
  solo ve los marcados como "compartido con Apple".
- tienda: solo ve "Mis casos" de los ship-to que se le asignen, con estado y fecha tentativa.
  Sin PO, precios, comentarios ni datos de otras tiendas.
El control está en el servidor: cada petición se valida contra la lista de rutas permitidas de su rol.
"""
import re
from collections import Counter
from datetime import timedelta

from . import db, logic

INTERNOS = ("admin", "editor", "lector")
NOMBRES = {"admin": "Administrador", "editor": "Editor", "lector": "Lector", "acm": "Apple Care Manager", "tienda": "Tienda"}
PERMITIDAS = {
    "acm": [("GET", r"/api/me"), ("GET", r"/api/cases"), ("GET", r"/api/cases/[^/]+"), ("GET", r"/api/kpis"),
            ("GET", r"/api/supply"), ("GET", r"/api/supply/[^/]+"), ("GET", r"/api/kgb-colombia"),
            ("POST", r"/api/cases/[^/]+/comments"), ("POST", r"/api/logout"), ("POST", r"/api/password")],
    "tienda": [("GET", r"/api/me"), ("GET", r"/api/tienda/casos"), ("POST", r"/api/logout"), ("POST", r"/api/password")],
}
_RX = {rol: [(m, re.compile(p + r"$")) for m, p in reglas] for rol, reglas in PERMITIDAS.items()}
EN_GESTION = {"En gestión", "Cotización enviada", "Pendiente aprobación ACM", "Sin solución"}
DIAS_DESPACHADOS = 15


def init_db() -> None:
    """Amplía los roles de la tabla de usuarios, agrega la visibilidad de los comentarios y las tiendas por usuario."""
    conn = db.connect()
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()["sql"]
        if "'acm'" not in sql:
            # SQLite no permite cambiar un CHECK: se recrea la tabla con los mismos datos.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript("""
                BEGIN;
                CREATE TABLE users_nueva(
                  id INTEGER PRIMARY KEY,
                  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                  full_name TEXT,
                  pw_hash TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('admin','editor','lector','acm','tienda')),
                  active INTEGER NOT NULL DEFAULT 1,
                  failed_attempts INTEGER NOT NULL DEFAULT 0,
                  locked_until TEXT,
                  must_change_pw INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL
                );
                INSERT INTO users_nueva(id, username, full_name, pw_hash, role, active, failed_attempts, locked_until,
                                        must_change_pw, created_at)
                  SELECT id, username, full_name, pw_hash, role, active, failed_attempts, locked_until, must_change_pw, created_at
                  FROM users;
                DROP TABLE users;
                ALTER TABLE users_nueva RENAME TO users;
                COMMIT;
            """)
            conn.execute("PRAGMA foreign_keys=ON")
        if "visibilidad" not in {r["name"] for r in conn.execute("PRAGMA table_info(comments)")}:
            conn.execute("ALTER TABLE comments ADD COLUMN visibilidad TEXT NOT NULL DEFAULT 'interno'")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_shipto(
                          user_id INTEGER NOT NULL REFERENCES users(id), ship_to TEXT NOT NULL,
                          PRIMARY KEY(user_id, ship_to))""")
        conn.commit()
    finally:
        conn.close()


def permitido(role: str, method: str, path: str) -> bool:
    if role in INTERNOS:
        return True
    metodo = "GET" if method == "HEAD" else method
    return any(metodo == m and rx.match(path) for m, rx in _RX.get(role, []))


def perfil(role: str) -> dict:
    """Lo que cada rol ve en pantalla (el servidor igual filtra los datos)."""
    if role == "acm":
        return {"pestanas": ["casos", "partes", "indicadores"], "ver_precios": False, "ver_miami": False,
                "puede_editar": False, "puede_comentar": True}
    if role == "tienda":
        return {"pestanas": ["tienda"], "ver_precios": False, "ver_miami": False, "puede_editar": False, "puede_comentar": False}
    return {"pestanas": None, "ver_precios": True, "ver_miami": True,
            "puede_editar": role in ("admin", "editor"), "puede_comentar": role in ("admin", "editor")}


# ---------------------------------------------------------------- filtros para Apple Care Manager
def caso(user, c: dict) -> dict:
    if user["role"] != "acm":
        return c
    c = dict(c)
    c.update(precio=None, precio_tienda=None, precio_apple=None, precios_especiales={}, compra_miami=None, inventario=None)
    c["alerts"] = [a for a in c["alerts"] if not a["code"].startswith(("MIAMI_", "INV_", "NOTA_ACM"))]
    niveles = {a["level"] for a in c["alerts"]}
    c["nivel"] = "rojo" if "rojo" in niveles else "ambar" if "ambar" in niveles else "info" if "info" in niveles else "ok"
    if c.get("llegada_fuente") in ("Compra Miami", "Inventario propio"):
        c["llegada_fuente"] = "Estimada por Ingram"
    return c


def comentarios(conn, user, key) -> list:
    if user["role"] == "acm":
        sql = "SELECT id, username, at, text, visibilidad FROM comments WHERE key_notif=? AND visibilidad='compartido'"
    else:
        sql = """SELECT m.id, m.username, m.at, m.text, m.visibilidad, u.role autor_rol, l.username leida_por, l.at leida_at
                 FROM comments m LEFT JOIN users u ON u.id = m.user_id LEFT JOIN notas_leidas l ON l.comment_id = m.id
                 WHERE m.key_notif=?"""
        return [dict(r) for r in conn.execute(sql + " ORDER BY m.id DESC", (key,))]
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", (key,))]


def gestion(user, filas: list) -> list:
    """Historial de gestión del equipo: Jenny solo ve estado, propuesta, aprobación y fechas, sin el origen."""
    if user["role"] != "acm":
        return filas
    import json
    visibles = {"estado", "propuesta", "aprobacion_kam", "fecha_recepcion", "fecha_despacho"}
    out = []
    for f in filas:
        d = {k: v for k, v in json.loads(f["detail"] or "{}").items() if k in visibles}
        if d:
            out.append({**f, "detail": json.dumps(d, ensure_ascii=False)})
    return out


def kpis(user, k: dict) -> dict:
    if user["role"] != "acm":
        return k
    k = dict(k)
    k["valor"] = {"total": None, "con_precio": 0, "sin_precio": 0}
    k["top_partes"] = [{**x, "valor": None} for x in k["top_partes"]]
    k["top_tiendas"] = [{**x, "valor": None} for x in k["top_tiendas"]]
    return k


def abastecimiento(user, r: dict) -> dict:
    if user["role"] != "acm":
        return r
    r["inv_fecha"] = None                          # el inventario propio es dato interno de Ingram
    for f in r["filas"]:
        f["inventario"] = None
        for a in f["alertas"]:
            if a["code"] == "SIN_ABASTO" and a["level"] != "rojo":
                a.update(level="ambar", text="Solo la trae la compra en Miami: no hay pedido a Apple ni tránsito.")
    return r


# ---------------------------------------------------------------- tiendas
def tiendas_de(conn, user_id: int) -> list:
    return [r["ship_to"] for r in conn.execute("SELECT ship_to FROM user_shipto WHERE user_id=? ORDER BY ship_to", (user_id,))]


def asignar(conn, admin, uid: int, ship_tos: list) -> list:
    limpio = sorted({str(s).strip() for s in ship_tos if str(s).strip()})[:500]
    antes = tiendas_de(conn, uid)
    conn.execute("DELETE FROM user_shipto WHERE user_id=?", (uid,))
    conn.executemany("INSERT INTO user_shipto(user_id, ship_to) VALUES(?,?)", [(uid, s) for s in limpio])
    db.audit(conn, admin, "user_tiendas", str(uid), {"antes": antes, "ahora": limpio})
    return limpio


def disponibles(conn) -> list:
    """Ship-to que aparecen en el backlog (abiertos o vistos en los últimos 60 días), con su nombre."""
    hasta = logic.iso(logic.today() - timedelta(days=60))
    filas = conn.execute(
        """SELECT ship_to, MAX(list_name) nombre, SUM(source_status='abierto') abiertos FROM cases
           WHERE ship_to IS NOT NULL AND (source_status='abierto' OR last_seen >= ?) GROUP BY ship_to""", (hasta,))
    return sorted(({"ship_to": r["ship_to"], "nombre": r["nombre"], "abiertos": r["abiertos"]} for r in filas),
                  key=lambda x: (x["nombre"] or "", x["ship_to"]))


def estado_tienda(c: dict, hoy: str):
    """(estado, fecha, qué es la fecha). Fecha tentativa = ETA de Apple, salvo lo que está en gestión."""
    if c.get("fecha_despacho"):
        return "Despachado", c["fecha_despacho"], "Despachado el"
    if c.get("llegada_fuente") in ("Recibido", "En inventario", "Inventario propio"):
        return "En bodega, por despachar", c.get("fecha_cliente"), "Despacho estimado"
    eta = c.get("eta_jenny")
    if c.get("estado") in EN_GESTION or not eta or c.get("option_jenny") == "To be Defined" or eta < hoy:
        return "En gestión", None, None
    return "En proceso", eta, "Fecha tentativa (ETA de Apple)"


def casos_tienda(conn, ctx, ship_tos: list) -> dict:
    hoy = ctx.today.isoformat()
    desde = logic.iso(ctx.today - timedelta(days=DIAS_DESPACHADOS))
    ultimo = conn.execute("SELECT MAX(file_date) m FROM loads WHERE kind='backlog'").fetchone()["m"]
    salida, nombres = [], {}
    if ship_tos:
        for c in logic.load_cases(conn, ctx, include_closed=True, closed_days=DIAS_DESPACHADOS + 30):
            if c["ship_to"] not in ship_tos:
                continue
            if c["fecha_despacho"]:
                if c["fecha_despacho"] < desde:
                    continue
            elif c["source_status"] != "abierto":
                continue
            nombres[c["ship_to"]] = c["list_name"]
            estado, fecha, tipo = estado_tienda(c, hoy)
            salida.append({"notificacion": f"{c['notifctn']}-{int(c['line_item'] or 0):02d}", "ship_to": c["ship_to"],
                           "tienda": c["list_name"], "parte": c["assembly"], "descripcion": c["description"],
                           "equipo": c.get("product_description") or c.get("material_desc"), "creado": c["created_on"],
                           "estado": estado, "fecha": fecha, "fecha_tipo": tipo})
    orden = {"En bodega, por despachar": 0, "En proceso": 1, "En gestión": 2, "Despachado": 3}
    salida.sort(key=lambda x: (orden[x["estado"]], x["fecha"] or "9999", x["notificacion"]))
    for s in ship_tos:
        if s not in nombres:
            r = conn.execute("SELECT MAX(list_name) n FROM cases WHERE ship_to=?", (s,)).fetchone()
            nombres[s] = r["n"] if r and r["n"] else s
    return {"actualizado": ultimo, "tiendas": [{"ship_to": s, "nombre": nombres[s]} for s in ship_tos],
            "dias_despachados": DIAS_DESPACHADOS, "resumen": dict(Counter(x["estado"] for x in salida)), "casos": salida}
