"""Bloque B: Open Orders de Apple, KGB con el porcentaje que va a Colombia y abastecimiento por parte.

Regla de Open Orders (verificada con los archivos del 5 al 13 de octubre de 2026):
una línea (orden + ítem) que sigue en Open Orders todavía no ha salido de Apple. Cuando Apple la asigna,
desaparece de Open Orders y aparece en el KGB con la misma orden e ítem, primero sin guía ("Ship Backlog")
y luego con guía y EDD. Que la orden ya tenga otras líneas en el KGB no dice nada de las que siguen pendientes.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import db, logic
from .ingest import (LoadError, _check_order, _columns, _empty, _register_load, _require, date_from_name,
                     read_table, to_date, to_num, to_str)

SCHEMA_B = """
CREATE TABLE IF NOT EXISTS open_orders(
  sales_ord TEXT NOT NULL, item TEXT NOT NULL, file_date TEXT NOT NULL,
  cust_num TEXT, ref_web TEXT, cust_po TEXT, tipo TEXT, plant TEXT, entry_date TEXT, req_date TEXT,
  part TEXT, part_norm TEXT, description TEXT, ord_qty REAL, remaining_qty REAL, scheduled_qty REAL,
  status TEXT, bloqueo TEXT, recovery_raw TEXT, recovery_date TEXT, salida_est TEXT, llegada_est TEXT,
  first_seen TEXT,
  PRIMARY KEY(sales_ord, item)
);
CREATE INDEX IF NOT EXISTS idx_oo_part ON open_orders(part_norm);
CREATE TABLE IF NOT EXISTS open_order_history(
  file_date TEXT NOT NULL, sales_ord TEXT NOT NULL, item TEXT NOT NULL, part TEXT, part_norm TEXT,
  remaining_qty REAL, status TEXT, recovery_date TEXT, salida_est TEXT,
  PRIMARY KEY(file_date, sales_ord, item)
);
CREATE INDEX IF NOT EXISTS idx_ooh_part ON open_order_history(part_norm);
"""
MIGRATIONS_B = [("kgb", "poso_item", "TEXT"), ("kgb", "ship_country", "TEXT")]

MESES = {"jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "jun": 6, "jul": 7,
         "aug": 8, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12, "dic": 12}
RE_RECOVERY = re.compile(r"(?:Recovery Date|End date)\s*:\s*(\d{4})-([A-Za-z]+)-(\d{1,2})", re.I)
OO_REQUIRED = ["Sales Ord#", "Item", "Material Num", "Remaining Qty", "Status", "Recovery ETA"]
OO_KNOWN = {"cust num", "customer name", "sales ord#", "ref/web #", "cust.req.date", "del blk", "ord entry date",
            "cust. po num.", "item", "plant", "material num", "material desc", "ord qty", "shpd qty", "delivery qty",
            "remaining qty", "scheduled qty", "sch line blocked for delv", "status", "recovery eta",
            # columnas de apoyo que Ferxxo agrega a mano en algunos archivos
            "llave", "to do lo que esta en backorder", "ref/web #/material num"}
SIN_ABASTO = ("OH", "FG", "Substitute Upgrade or/and Color change")   # soluciones que no esperan esta parte


def init_db() -> None:
    """Crea las tablas del bloque B y agrega columnas nuevas al KGB. Se llama al arrancar."""
    conn = db.connect()
    try:
        conn.executescript(SCHEMA_B)
        for table, column, kind in MIGRATIONS_B:
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- utilidades
def fecha_nombre(name: str):
    """'Colombia-Open Orders Report-Oct.13.2026.xlsx' -> 2026-10-13. Si no, usa el formato de siempre."""
    for m in re.finditer(r"([A-Za-z]{3})[a-z]*[._\- ]?(\d{1,2})[._\-, ]+(20\d{2})", name):
        mes = MESES.get(m.group(1).lower())
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(2))).isoformat()
            except ValueError:
                pass
    return date_from_name(name)


def _proximo_mar_jue(x: date) -> date:
    y = x + timedelta(days=1)
    while y.weekday() not in (1, 3):        # martes o jueves
        y += timedelta(days=1)
    return y


def parse_recovery(texto, status, file_date: date):
    """Devuelve (fecha de recovery, salida estimada de Apple). Recovery es cuando Apple tendrá stock."""
    t = (texto or "").strip()
    m = RE_RECOVERY.search(t)
    if m:
        mes = MESES.get(m.group(2)[:3].lower())
        try:
            rec = date(int(m.group(1)), mes, int(m.group(3))) if mes else None
        except ValueError:
            rec = None
        if rec:
            return rec, max(rec, file_date)       # si ya pasó y sigue aquí, lo más pronto es hoy
    st = (status or "").strip().lower()
    if "tues/thur" in t.lower() or (st == "shipment pending" and not t):
        return None, _proximo_mar_jue(file_date)
    return None, None


def tipo_orden(cust_po) -> str:
    s = (cust_po or "").strip().upper()
    if s.startswith("CHANNELFOC_NPI"):
        return "NPI"
    if s.startswith("CHANNELFOC"):
        return "Channel FOC"
    if s.isdigit():
        return "PO"
    return "Otra"


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


# ---------------------------------------------------------------- carga de Open Orders
def load_open_orders(conn, data: bytes, name: str, fecha, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or fecha_nombre(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    _check_order(conn, "openorders", file_date, file_hash)
    df = next(iter(read_table(data, name).values()))
    # Algunos archivos traen notas en el encabezado: "Remaining Qty (FALTA POR ENTREGRAR"
    df.columns = [re.split(r"\s*\(", str(c))[0].strip() for c in df.columns]
    _require(df, OO_REQUIRED, "Open Orders")
    nuevas_cols = [c for c in df.columns if c.lower() not in OO_KNOWN and not c.lower().startswith("unnamed")]
    df = df[df["Sales Ord#"].map(lambda v: not _empty(v))]
    total = len(df)
    settings = db.get_settings(conn)
    wanted = {c.strip().lstrip("0") for c in settings.get("kgb_cust_ids", "").split(",") if c.strip()}
    if wanted and "Cust Num" in df.columns:
        df = df[df["Cust Num"].map(lambda v: (to_str(v) or "").lstrip("0")).isin(wanted)]
    if df.empty:
        raise LoadError("Ninguna línea del Open Orders corresponde al cliente configurado (Cliente KGB en Administración).")
    transito = int(settings.get("oo_transit_days", "22"))
    hoy = date.fromisoformat(file_date)
    prev = {(r["sales_ord"], r["item"]): dict(r) for r in conn.execute("SELECT * FROM open_orders")}

    conn.execute("DELETE FROM open_orders")
    conn.execute("DELETE FROM open_order_history WHERE file_date=?", (file_date,))
    seen, ordenes, partes, unidades, sin_fecha, tipos = set(), set(), set(), 0.0, 0, Counter()
    for _, r in df.iterrows():
        orden, item = to_str(r["Sales Ord#"]), to_str(r["Item"])
        if not item or (orden, item) in seen:
            continue
        seen.add((orden, item))
        part = (to_str(r["Material Num"]) or "").upper()
        qty = to_num(r["Remaining Qty"]) or 0
        status = (to_str(r["Status"]) or "").strip()
        raw = to_str(r["Recovery ETA"])
        rec, salida = parse_recovery(raw, status, hoy)
        llegada = salida + timedelta(days=transito) if salida else None
        cust_po = to_str(r.get("Cust. PO num."))
        bloqueo = ", ".join(x for x in (to_str(r.get("Del Blk")), to_str(r.get("Sch line blocked for delv"))) if x) or None
        old = prev.get((orden, item))
        rec_row = {
            "sales_ord": orden, "item": item, "file_date": file_date, "cust_num": to_str(r.get("Cust Num")),
            "ref_web": to_str(r.get("Ref/Web #")), "cust_po": cust_po, "tipo": tipo_orden(cust_po),
            "plant": to_str(r.get("Plant")), "entry_date": to_date(r.get("Ord Entry Date")),
            "req_date": to_date(r.get("Cust.Req.Date")), "part": part, "part_norm": logic.norm_part(part),
            "description": to_str(r.get("Material Desc")), "ord_qty": to_num(r.get("Ord Qty")), "remaining_qty": qty,
            "scheduled_qty": to_num(r.get("Scheduled qty")), "status": status, "bloqueo": bloqueo, "recovery_raw": raw,
            "recovery_date": logic.iso(rec), "salida_est": logic.iso(salida), "llegada_est": logic.iso(llegada),
            "first_seen": old["first_seen"] if old else file_date,
        }
        conn.execute(f"INSERT INTO open_orders({','.join(rec_row)}) VALUES({','.join('?' * len(rec_row))})", list(rec_row.values()))
        conn.execute("""INSERT OR REPLACE INTO open_order_history(file_date, sales_ord, item, part, part_norm, remaining_qty,
                        status, recovery_date, salida_est) VALUES(?,?,?,?,?,?,?,?,?)""",
                     (file_date, orden, item, part, rec_row["part_norm"], qty, status, rec_row["recovery_date"], rec_row["salida_est"]))
        ordenes.add(orden)
        partes.add(rec_row["part_norm"])
        unidades += qty
        sin_fecha += salida is None
        tipos[rec_row["tipo"]] += 1
    stats = {"filas_archivo": int(total), "lineas": len(seen), "ordenes": len(ordenes), "partes": len(partes),
             "unidades": int(unidades), "sin_fecha": sin_fecha, "tipos": dict(tipos),
             "nuevas": len([k for k in seen if k not in prev]), "salieron": len([k for k in prev if k not in seen]),
             "columnas_nuevas": nuevas_cols}
    _register_load(conn, "openorders", name, file_hash, file_date, user, len(seen), stats)
    db.audit(conn, user, "load_openorders", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- carga del KGB (reemplaza a ingest.load_kgb)
def load_kgb(conn, data: bytes, name: str, fecha, user) -> dict:
    """Igual que antes, más: orden + ítem de Apple, país de destino y cuánto del KGB va a Colombia."""
    from .ingest import KGB_REQUIRED
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    _check_order(conn, "kgb", file_date, file_hash)
    df = _columns(next(iter(read_table(data, name).values())))
    _require(df, KGB_REQUIRED, "KGB")

    wanted = {c.strip().lstrip("0") for c in db.get_settings(conn).get("kgb_cust_ids", "").split(",") if c.strip()}
    df["_cust"] = df["CUST_ID"].map(lambda v: (to_str(v) or "").lstrip("0"))
    df["_qty"] = df["DELIVERY_QTY"].map(lambda v: to_num(v) or 0)
    df["_trk"] = df["TRACKING_NR"].map(to_str)
    if "SHIP_TO_COUNTRY_CD" in df.columns:
        co = df[df["SHIP_TO_COUNTRY_CD"].map(lambda v: (to_str(v) or "").upper() == "CO")]
    else:
        co = df[df["_cust"].isin(wanted)]
    total, unid_total = len(df), float(df["_qty"].sum())
    full = df
    if wanted:
        df = df[df["_cust"].isin(wanted)]
    if df.empty:
        raise LoadError("Ninguna fila del KGB corresponde al cliente configurado. Revise 'Cliente KGB' en Administración.")
    prev = conn.execute("SELECT file_date, summary FROM loads WHERE kind='kgb' ORDER BY file_date DESC, id DESC LIMIT 1").fetchone()

    conn.execute("DELETE FROM kgb")
    for _, r in df.iterrows():
        prod = to_str(r["PROD_ID"])
        conn.execute(
            """INSERT INTO kgb(tracking, delivery_id, delivery_item, poso, poso_item, prod_id, prod_norm, prod_desc, status,
               current_event, event_dt, edd, pgi_dt, sla_ind, issue, qty, cust_id, ship_country)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["_trk"], to_str(r["DELIVERY_ID"]), to_str(r.get("DELIVERY_ITEM_NR")), to_str(r.get("POSO_NR")),
             to_str(r.get("POSO_ITEM_NR")), prod, logic.norm_part(prod), to_str(r.get("PROD_DESC")), to_str(r["STATUS_DESC"]),
             to_str(r["CURRENT_EVENT"]), to_date(r.get("CURRENT_EVENT_DT")), to_date(r["EDD"]), to_date(r.get("PGI_DT")),
             to_str(r.get("IN_OUT_SLA_IND")), to_str(r.get("ISSUE_DESC")), r["_qty"], r["_cust"],
             to_str(r.get("SHIP_TO_COUNTRY_CD"))))
    es661 = full["PROD_ID"].map(lambda v: logic.norm_part(to_str(v)).startswith("661-"))
    transito = full["STATUS_DESC"].map(lambda v: (to_str(v) or "").lower() == "in transit")
    co_mask = full.index.isin(co.index)
    u = lambda m: float(full.loc[m, "_qty"].sum())
    stats = {"filas_archivo": int(total), "filas_cliente": int(len(df)), "guias": int(df["_trk"].dropna().nunique()),
             "unid_archivo": int(round(unid_total)), "filas_co": int(len(co)), "unid_co": int(round(co["_qty"].sum())),
             "guias_archivo": int(full["_trk"].dropna().nunique()), "guias_co": int(co["_trk"].dropna().nunique()),
             "pct_filas_co": _pct(len(co), total), "pct_unid_co": _pct(co["_qty"].sum(), unid_total),
             "unid_661": int(u(es661)), "unid_661_co": int(u(es661 & co_mask)), "pct_unid_661_co": _pct(u(es661 & co_mask), u(es661)),
             "unid_transito": int(u(transito)), "unid_transito_co": int(u(transito & co_mask)),
             "pct_unid_transito_co": _pct(u(transito & co_mask), u(transito)),
             "unid_co_consumibles": int(u(co_mask & ~es661))}
    if prev:
        p = json.loads(prev["summary"] or "{}")
        stats["fecha_ant"] = prev["file_date"]
        stats["pct_filas_co_ant"] = p.get("pct_filas_co", _pct(p.get("filas_cliente", 0), p.get("filas_archivo", 0)))
        stats["pct_unid_co_ant"] = p.get("pct_unid_co")
    _register_load(conn, "kgb", name, file_hash, file_date, user, int(len(df)), stats)
    db.audit(conn, user, "load_kgb", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


def kgb_historial(conn, n: int = 15) -> list:
    """Cuánto del KGB va a Colombia en cada carga y el cambio frente a la anterior (en puntos)."""
    out = []
    for r in conn.execute("SELECT file_date, summary FROM loads WHERE kind='kgb' ORDER BY file_date, id"):
        s = json.loads(r["summary"] or "{}")
        pf = s.get("pct_filas_co", _pct(s.get("filas_cliente", 0), s.get("filas_archivo", 0)))
        out.append({"fecha": r["file_date"], "filas_co": s.get("filas_co", s.get("filas_cliente")),
                    "filas": s.get("filas_archivo"), "pct_filas": pf, "unid_co": s.get("unid_co"),
                    "unid": s.get("unid_archivo"), "pct_unid": s.get("pct_unid_co"),
                    "pct_unid_661": s.get("pct_unid_661_co"), "unid_661_co": s.get("unid_661_co"), "unid_661": s.get("unid_661"),
                    "pct_unid_transito": s.get("pct_unid_transito_co"), "unid_co_otros": s.get("unid_co_consumibles"),
                    "guias_co": s.get("guias_co", s.get("guias")), "guias": s.get("guias_archivo")})
    for a, b in zip(out, out[1:]):
        b["var_filas"] = round(b["pct_filas"] - a["pct_filas"], 1) if None not in (a["pct_filas"], b["pct_filas"]) else None
        b["var_unid"] = round(b["pct_unid"] - a["pct_unid"], 1) if None not in (a["pct_unid"], b["pct_unid"]) else None
    return out[-n:][::-1]


# ---------------------------------------------------------------- abastecimiento por parte
def _salidas(conn):
    """Líneas que salieron de Open Orders en la última carga y qué pasó con ellas."""
    fechas = [r["file_date"] for r in conn.execute(
        "SELECT DISTINCT file_date FROM open_order_history ORDER BY file_date DESC LIMIT 2")]
    if len(fechas) < 2:
        return {"desde": None, "hasta": fechas[0] if fechas else None, "lineas": [], "conteo": {}}
    hasta, desde = fechas
    actual = {(r["sales_ord"], r["item"]) for r in conn.execute("SELECT sales_ord, item FROM open_orders")}
    actual_parte = {(r["sales_ord"], r["part_norm"]) for r in conn.execute("SELECT sales_ord, part_norm FROM open_orders")}
    kgb_item = {(r["poso"], r["poso_item"]): r for r in conn.execute("SELECT * FROM kgb WHERE poso_item IS NOT NULL")}
    kgb_parte = {(r["poso"], r["prod_norm"]): r for r in conn.execute("SELECT * FROM kgb")}
    lineas, conteo = [], Counter()
    for r in conn.execute("SELECT * FROM open_order_history WHERE file_date=?", (desde,)):
        k = (r["sales_ord"], r["item"])
        if k in actual:
            continue
        kg = kgb_item.get(k) or kgb_parte.get((r["sales_ord"], r["part_norm"]))
        if kg:
            res = "En el KGB" + (f" (guía {kg['tracking']})" if kg["tracking"] else " (sin guía todavía)")
            clave = "kgb"
        elif (r["sales_ord"], r["part_norm"]) in actual_parte:
            res, clave = "Sigue en Open Orders con otro ítem", "otro_item"
        else:
            res, clave = "Sin rastro: cancelada o aún no aparece en el KGB", "sin_rastro"
        conteo[clave] += 1
        lineas.append({"orden": r["sales_ord"], "item": r["item"], "parte": r["part"], "part_norm": r["part_norm"],
                       "cantidad": r["remaining_qty"], "estado": r["status"], "resultado": res, "clave": clave})
    return {"desde": desde, "hasta": hasta, "lineas": lineas, "conteo": dict(conteo)}


def _oferta(conn):
    oo = defaultdict(lambda: {"qty": 0.0, "lineas": 0, "ordenes": set(), "salida": None, "llegada": None,
                              "sin_fecha": 0.0, "estados": Counter(), "tipos": Counter(), "desc": None})
    for r in conn.execute("SELECT * FROM open_orders"):
        a = oo[r["part_norm"]]
        a["qty"] += r["remaining_qty"] or 0
        a["lineas"] += 1
        a["ordenes"].add(r["sales_ord"])
        a["estados"][r["status"] or "—"] += r["remaining_qty"] or 0
        a["tipos"][r["tipo"]] += 1
        a["desc"] = a["desc"] or r["description"]
        if r["salida_est"]:
            a["salida"] = min(a["salida"] or r["salida_est"], r["salida_est"])
            a["llegada"] = min(a["llegada"] or r["llegada_est"], r["llegada_est"])
        else:
            a["sin_fecha"] += r["remaining_qty"] or 0
    kg = defaultdict(lambda: {"con_guia": 0.0, "sin_guia": 0.0, "edd": None, "guias": set()})
    for r in conn.execute("SELECT * FROM kgb"):
        a = kg[r["prod_norm"]]
        if r["tracking"]:
            a["con_guia"] += r["qty"] or 0
            a["guias"].add(r["tracking"])
            if r["edd"]:
                a["edd"] = min(a["edd"] or r["edd"], r["edd"])
        else:
            a["sin_guia"] += r["qty"] or 0
    return oo, kg


def _suma(partes, tabla, campos):
    out = {}
    for f in campos:
        vals = [tabla[p][f] for p in partes if p in tabla]
        if f in ("qty", "con_guia", "sin_guia", "sin_fecha", "lineas"):
            out[f] = sum(vals)
        elif f in ("salida", "llegada", "edd"):
            out[f] = min((v for v in vals if v), default=None)
        elif f in ("ordenes", "guias"):
            out[f] = sorted(set().union(*vals)) if vals else []
    return out


def por_parte(conn, ctx, incluir_sin_casos: bool = False) -> dict:
    """Una fila por parte: demanda del backlog, KGB (con y sin guía) y Open Orders, con alertas."""
    casos = [c for c in logic.load_cases(conn, ctx) if c["pendiente"]]
    oo, kg = _oferta(conn)
    salidas = _salidas(conn)
    sal_por_parte = Counter(x["part_norm"] for x in salidas["lineas"] if x["clave"] == "kgb")
    dem = defaultdict(lambda: {"casos": 0, "necesitan": 0, "miami": 0, "rojos": 0, "opciones": Counter(), "limite": None,
                               "parte": None, "desc": None})
    for c in casos:
        p = logic.norm_part(c["assembly"])
        a = dem[p]
        a["casos"] += 1
        a["necesitan"] += c["option_jenny"] not in SIN_ABASTO
        a["miami"] += c["option_jenny"] == "Miami"
        a["miami_compra"] = a.get("miami_compra", 0) + bool(c.get("compra_miami"))
        a["rojos"] += c["nivel"] == "rojo"
        a["opciones"][c["option_short"]] += 1
        a["parte"] = a["parte"] or c["assembly"]
        a["desc"] = a["desc"] or c["description"]
        if c["limite_fr"] and c["option_jenny"] not in SIN_ABASTO:
            a["limite"] = min(a["limite"] or c["limite_fr"], c["limite_fr"])
    claves = set(dem) | (set(oo) if incluir_sin_casos else set())
    filas = []
    for p in claves:
        d = dem.get(p) or {"casos": 0, "necesitan": 0, "miami": 0, "rojos": 0, "opciones": Counter(), "limite": None,
                           "parte": None, "desc": None}
        master = ctx.parts_norm.get(p) or {}
        sub = logic.norm_part(master.get("substitute")) if master.get("substitute") else None
        grupo = [p] + ([sub] if sub and sub != p else [])
        o = _suma(grupo, oo, ("qty", "lineas", "salida", "llegada", "sin_fecha", "ordenes"))
        k = _suma(grupo, kg, ("con_guia", "sin_guia", "edd", "guias"))
        inv_qty = sum(ctx.inv_por_parte.get(x, 0) for x in grupo)
        estados = Counter()
        for x in grupo:
            if x in oo:
                estados.update(oo[x]["estados"])
        alertas = []
        if d["necesitan"] and not o["qty"] and not (k["con_guia"] + k["sin_guia"]) and not inv_qty:
            compradas = d.get("miami_compra", 0)
            if d["necesitan"] > d["miami"]:
                alertas.append({"code": "SIN_ABASTO", "level": "rojo",
                                "text": "Parte sin pedido ni tránsito: no está en Open Orders ni en el KGB."})
            elif compradas < d["miami"]:
                alertas.append({"code": "SIN_ABASTO", "level": "ambar",
                                "text": f"Solo la trae la compra en Miami ({compradas} de {d['miami']} casos con compra registrada)."})
            else:
                alertas.append({"code": "SIN_ABASTO", "level": "info",
                                "text": "Solo la trae Miami; " + ("el caso tiene compra registrada." if d["miami"] == 1
                                                                  else f"los {d['miami']} casos tienen compra registrada.")})
        if d["necesitan"] and o["sin_fecha"] and not (k["con_guia"] + k["sin_guia"]):
            alertas.append({"code": "PEDIDO_SIN_FECHA", "level": "ambar",
                            "text": f"{int(o['sin_fecha'])} unid. pedidas sin Recovery ETA en Open Orders."})
        if d["necesitan"] and o["llegada"] and d["limite"] and o["llegada"] > d["limite"] and not k["con_guia"]:
            alertas.append({"code": "PEDIDO_TARDE", "level": "info",
                            "text": f"Lo pedido llegaría hacia el {logic.fmt(logic.d(o['llegada']))}, después del límite más cercano ({logic.fmt(logic.d(d['limite']))})."})
        nivel = "rojo" if any(a["level"] == "rojo" for a in alertas) else "ambar" if any(a["level"] == "ambar" for a in alertas) else "info" if alertas else "ok"
        filas.append({
            "parte": d["parte"] or next((r["part"] for r in conn.execute("SELECT part FROM open_orders WHERE part_norm=? LIMIT 1", (p,))), p),
            "part_norm": p, "descripcion": d["desc"] or master.get("description") or (oo[p]["desc"] if p in oo else None),
            "sustituto": master.get("substitute"), "casos": d["casos"], "necesitan": d["necesitan"], "rojos": d["rojos"],
            "opciones": dict(d["opciones"]), "limite": d["limite"],
            "kgb_con_guia": k["con_guia"], "kgb_sin_guia": k["sin_guia"], "kgb_edd": k["edd"], "kgb_guias": k["guias"],
            "oo_qty": o["qty"], "oo_lineas": o["lineas"], "oo_ordenes": len(o["ordenes"]), "oo_salida": o["salida"],
            "oo_llegada": o["llegada"], "oo_sin_fecha": o["sin_fecha"], "oo_estado": estados.most_common(1)[0][0] if estados else None,
            "salieron_al_kgb": sum(sal_por_parte[x] for x in grupo),
            "inventario": inv_qty,
            "alertas": alertas, "nivel": nivel,
        })
    orden = {"rojo": 0, "ambar": 1, "info": 2, "ok": 3}
    filas.sort(key=lambda f: (orden[f["nivel"]], -f["rojos"], -f["casos"], f["parte"] or ""))
    con_casos = [f for f in filas if f["casos"]]
    oo_fecha = conn.execute("SELECT MAX(file_date) m FROM loads WHERE kind='openorders'").fetchone()["m"]
    tot = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(remaining_qty),0) q, COUNT(DISTINCT sales_ord) o FROM open_orders").fetchone()
    return {
        "oo_fecha": oo_fecha, "kgb_fecha": ctx.kgb_date, "transito_dias": int(ctx.settings.get("oo_transit_days", "22")),
        "inv_fecha": ctx.inv_fecha,
        "resumen": {"partes_con_casos": len(con_casos),
                    "sin_abasto": sum(1 for f in con_casos if any(a["code"] == "SIN_ABASTO" and a["level"] == "rojo" for a in f["alertas"])),
                    "sin_abasto_casos": sum(f["necesitan"] for f in con_casos if any(a["code"] == "SIN_ABASTO" and a["level"] == "rojo" for a in f["alertas"])),
                    "solo_miami": sum(1 for f in con_casos if any(a["code"] == "SIN_ABASTO" and a["level"] != "rojo" for a in f["alertas"])),
                    "solo_miami_casos": sum(f["necesitan"] for f in con_casos if any(a["code"] == "SIN_ABASTO" and a["level"] != "rojo" for a in f["alertas"])),
                    "pedido_sin_fecha": sum(1 for f in con_casos if any(a["code"] == "PEDIDO_SIN_FECHA" for a in f["alertas"])),
                    "con_pedido": sum(1 for f in con_casos if f["oo_qty"]),
                    "oo_lineas": tot["n"], "oo_unidades": int(tot["q"]), "oo_ordenes": tot["o"]},
        "salidas": {k: v for k, v in salidas.items() if k != "lineas"},
        "filas": filas,
    }


def detalle_parte(conn, ctx, part: str) -> dict:
    """Open Orders, KGB, salidas recientes e historia de una parte (y su sustituto oficial)."""
    p = logic.norm_part(part)
    master = ctx.parts_norm.get(p) or {}
    sub = logic.norm_part(master.get("substitute")) if master.get("substitute") else None
    grupo = [p] + ([sub] if sub and sub != p else [])
    marcas = ",".join("?" * len(grupo))
    oo = [dict(r) for r in conn.execute(
        f"SELECT * FROM open_orders WHERE part_norm IN ({marcas}) ORDER BY COALESCE(salida_est, '9999'), sales_ord, item", grupo)]
    kgb = [{k: r[k] for k in ("tracking", "poso", "poso_item", "prod_id", "qty", "edd", "status", "current_event")}
           for r in conn.execute(f"SELECT * FROM kgb WHERE prod_norm IN ({marcas}) ORDER BY COALESCE(edd, '9999')", grupo)]
    hist = [dict(r) for r in conn.execute(
        f"""SELECT file_date, SUM(remaining_qty) unidades, COUNT(*) lineas FROM open_order_history
            WHERE part_norm IN ({marcas}) GROUP BY file_date ORDER BY file_date DESC LIMIT 10""", grupo)]
    salidas = [x for x in _salidas(conn)["lineas"] if x["part_norm"] in grupo]
    casos = sum(1 for r in conn.execute("SELECT assembly FROM cases WHERE source_status='abierto' AND fecha_despacho IS NULL")
                if logic.norm_part(r["assembly"]) in grupo)
    return {"parte": part, "part_norm": p, "sustituto": master.get("substitute"), "casos_pendientes": casos,
            "open_orders": oo, "kgb": kgb, "historia": hist, "salidas": salidas,
            "transito_dias": int(ctx.settings.get("oo_transit_days", "22"))}
