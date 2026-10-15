"""Inventario propio (on hand de Ingram): carga, asignación a casos y alertas.

Todavía no hay un formato oficial, así que la carga reconoce columnas por nombre:
parte (Material, Part Number, Número de pieza, SKU…), cantidad (Libre utilización, Unrestricted, On hand,
Cantidad, Qty…) y, si vienen, bodega y descripción. El encabezado puede estar en cualquiera de las 10 primeras filas.

Con el inventario cargado, las unidades se reparten entre los casos pendientes por prioridad
(OH primero, luego Super ASP, luego la ETA más cercana; si la parte se acaba, se usa el sustituto oficial):
- caso con unidad asignada: "se puede despachar con inventario propio" y su llegada pasa a ser la fecha del archivo;
- caso OH de Jenny sin unidad asignada: alerta "OH sin inventario";
- la vista de Abastecimiento muestra desde cuándo había inventario (para ver lo que se debió despachar).
"""
import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import date

from . import db, logic
from .ingest import LoadError, _check_order, _register_load, date_from_name, read_raw, to_num, to_str

SCHEMA_INV = """
CREATE TABLE IF NOT EXISTS inventario(
  part TEXT, part_norm TEXT, cantidad REAL, bodega TEXT, descripcion TEXT, file_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_inv_part ON inventario(part_norm);
CREATE TABLE IF NOT EXISTS inventario_history(
  file_date TEXT NOT NULL, part_norm TEXT NOT NULL, cantidad REAL,
  PRIMARY KEY(file_date, part_norm)
);
"""
COL_PARTE = ("material", "material num", "material number", "numero de material", "material no", "part", "part number",
             "parte", "numero de parte", "numero de pieza", "sku", "assembly", "prod_id", "item", "codigo", "referencia")
COL_CANT = ("libre utilizacion", "unrestricted", "unrestricted stock", "unrestr. use", "on hand", "onhand", "oh",
            "disponible", "available", "cantidad", "qty", "quantity", "stock", "existencia", "inventario", "saldo")
COL_BODEGA = ("almacen", "storage location", "sloc", "bodega", "location", "ubicacion", "centro", "plant")
COL_DESC = ("descripcion", "texto breve de material", "material description", "description", "part description")


def init_db() -> None:
    conn = db.connect()
    try:
        conn.executescript(SCHEMA_INV)
        conn.commit()
    finally:
        conn.close()


def _norm(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s.replace("\n", " ")).strip()


def _col(headers, opciones):
    for op in opciones:                       # el orden de la lista es la preferencia
        for i, h in enumerate(headers):
            if h == op:
                return i
    return None


def load_inventario(conn, data: bytes, name: str, fecha, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name) or date.today().isoformat()
    _check_order(conn, "inventario", file_date, file_hash)
    raw = read_raw(data, name)
    fila, cols = None, None
    for i in range(min(10, len(raw))):
        headers = [_norm(v) for v in raw.iloc[i].tolist()]
        p, q = _col(headers, COL_PARTE), _col(headers, COL_CANT)
        if p is not None and q is not None:
            fila, cols = i, {"parte": p, "cantidad": q, "bodega": _col(headers, COL_BODEGA), "desc": _col(headers, COL_DESC)}
            nombres = [str(v).replace("\n", " ").strip() for v in raw.iloc[i].tolist()]
            break
    if fila is None:
        raise LoadError("No encontré las columnas de parte y cantidad. Use encabezados como 'Parte' y 'Cantidad' "
                        "(o Material / Libre utilización, Part Number / On hand). Puede descargar la plantilla.")
    total = defaultdict(float)
    filas, desc = [], {}
    for row in raw.iloc[fila + 1:].itertuples(index=False):
        part = (to_str(row[cols["parte"]]) or "").upper()
        qty = to_num(row[cols["cantidad"]])
        if not part or qty is None or qty <= 0 or not re.search(r"\d", part):
            continue
        bodega = to_str(row[cols["bodega"]]) if cols["bodega"] is not None else None
        d = to_str(row[cols["desc"]]) if cols["desc"] is not None else None
        filas.append((part, logic.norm_part(part), qty, bodega, d, file_date))
        total[logic.norm_part(part)] += qty
        if d:
            desc.setdefault(logic.norm_part(part), d)
    if not filas:
        raise LoadError("El archivo no trae partes con cantidad mayor que cero.")
    conn.execute("DELETE FROM inventario")
    conn.executemany("INSERT INTO inventario(part, part_norm, cantidad, bodega, descripcion, file_date) VALUES(?,?,?,?,?,?)", filas)
    conn.execute("DELETE FROM inventario_history WHERE file_date=?", (file_date,))
    conn.executemany("INSERT INTO inventario_history(file_date, part_norm, cantidad) VALUES(?,?,?)",
                     [(file_date, p, q) for p, q in total.items()])
    usadas = {k: nombres[v] for k, v in cols.items() if v is not None}
    stats = {"filas": len(filas), "partes": len(total), "unidades": int(sum(total.values())),
             "bodegas": len({f[3] for f in filas if f[3]}), "columnas": usadas}
    _register_load(conn, "inventario", name, file_hash, file_date, user, len(filas), stats)
    db.audit(conn, user, "load_inventario", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- contexto y asignación
def cargar_contexto(ctx, conn) -> None:
    """Se llama al final de Context.__init__. Reparte el inventario entre los casos pendientes por prioridad."""
    ctx.inv_fecha, ctx.inv_por_parte, ctx.inv_asignado, ctx.inv_alertas = None, {}, {}, alertas
    try:
        fila = conn.execute("SELECT MAX(file_date) m FROM loads WHERE kind='inventario'").fetchone()
        if not fila or not fila["m"]:
            return
        ctx.inv_fecha = fila["m"]
        for r in conn.execute("SELECT part_norm, SUM(cantidad) q FROM inventario GROUP BY part_norm"):
            ctx.inv_por_parte[r["part_norm"]] = r["q"]
    except Exception:
        return
    libre = dict(ctx.inv_por_parte)
    pendientes = [dict(r) for r in conn.execute(
        """SELECT key_notif, assembly, option_jenny, eta_jenny, created_on, repair_strategy FROM cases
           WHERE source_status='abierto' AND fecha_despacho IS NULL AND fecha_recepcion IS NULL""")]
    pendientes.sort(key=lambda c: (c["option_jenny"] != "OH", (c["repair_strategy"] or "").upper() != "Z8.2",
                                   c["eta_jenny"] or "9999", c["created_on"] or "9999"))
    for c in pendientes:
        p = logic.norm_part(c["assembly"])
        master = ctx.parts_norm.get(p) or {}
        sub = logic.norm_part(master.get("substitute")) if master.get("substitute") else None
        for parte, via in ((p, "parte"), (sub, "sustituto")):
            if parte and libre.get(parte, 0) >= 1:
                libre[parte] -= 1
                ctx.inv_asignado[c["key_notif"]] = {"parte": parte, "via": via}
                break


def alertas(c, ctx, inv) -> list:
    """Alertas de inventario para un caso pendiente."""
    if not ctx.inv_fecha:
        return []
    fecha = logic.fmt(logic.d(ctx.inv_fecha))
    opt = c.get("option_jenny") or ""
    if inv and opt != "OH":
        extra = " (con el sustituto oficial)" if inv["via"] == "sustituto" else ""
        return [{"code": "INV_DISPONIBLE", "level": "ambar",
                 "text": f"Hay inventario propio de {inv['parte']}{extra} en el archivo del {fecha}: este caso se puede despachar."}]
    if opt == "OH" and not inv and not c.get("fecha_recepcion"):
        return [{"code": "INV_OH_SIN_STOCK", "level": "ambar",
                 "text": f"Jenny lo marca OH, pero el inventario del {fecha} no tiene unidades libres de esta parte."}]
    return []


def resumen(conn, ctx) -> dict:
    """Panel de Abastecimiento: lo que se puede (o se pudo) despachar con el inventario propio."""
    if not ctx.inv_fecha:
        return {"fecha": None}
    hist = defaultdict(dict)
    for r in conn.execute("SELECT file_date, part_norm, cantidad FROM inventario_history"):
        hist[r["part_norm"]][r["file_date"]] = r["cantidad"]
    fechas = [r["file_date"] for r in conn.execute("SELECT DISTINCT file_date FROM inventario_history ORDER BY file_date")]
    casos, oh_sin = [], []
    for c in logic.load_cases(conn, ctx):
        if not c["pendiente"]:
            continue
        inv = ctx.inv_asignado.get(c["key_notif"])
        if inv and c["option_jenny"] != "OH":
            desde = None
            for f in reversed(fechas):           # desde cuándo hay inventario sin interrupción
                if hist[inv["parte"]].get(f, 0) > 0 and f >= (c["first_seen"] or ""):
                    desde = f
                else:
                    break
            casos.append({"key_notif": c["key_notif"], "tienda": c["list_name"], "parte": c["assembly"],
                          "via": inv["via"], "opcion": c["option_short"], "eta": c["eta_jenny"], "limite": c["limite_fr"],
                          "desde": desde, "dias": ctx.cal.business_days_between(logic.d(desde), ctx.today) if desde else None,
                          "nivel": c["nivel"]})
        elif c["option_jenny"] == "OH" and not inv and not c["fecha_recepcion"]:
            oh_sin.append({"key_notif": c["key_notif"], "tienda": c["list_name"], "parte": c["assembly"], "eta": c["eta_jenny"]})
    casos.sort(key=lambda x: (-(x["dias"] or 0), x["limite"] or "9999"))
    tot = conn.execute("SELECT COUNT(DISTINCT part_norm) p, COALESCE(SUM(cantidad),0) q FROM inventario").fetchone()
    return {"fecha": ctx.inv_fecha, "partes": tot["p"], "unidades": int(tot["q"]),
            "asignadas": len(ctx.inv_asignado), "casos": casos, "oh_sin_inventario": oh_sin}
