"""Carga del KGB y de Open Orders, porcentaje del KGB que va a Colombia y abastecimiento por parte."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from ..domain.abastecimiento import SIN_ABASTO, date_from_name, fecha_nombre, parse_recovery, pct, tipo_orden
from ..domain.calendario import d, fmt, iso
from ..domain.errores import LoadError
from ..domain.partes import norm_part
from . import contexto
from .archivos import _empty, columns, read_table, require, to_date, to_num, to_str
from .auditoria import audit, get_settings
from .cargas import KGB_REQUIRED, check_order, register_load

OO_REQUIRED = ["Sales Ord#", "Item", "Material Num", "Remaining Qty", "Status", "Recovery ETA"]
OO_KNOWN = {"cust num", "customer name", "sales ord#", "ref/web #", "cust.req.date", "del blk", "ord entry date",
            "cust. po num.", "item", "plant", "material num", "material desc", "ord qty", "shpd qty", "delivery qty",
            "remaining qty", "scheduled qty", "sch line blocked for delv", "status", "recovery eta",
            # columnas de apoyo que se agregan a mano en algunos archivos
            "llave", "to do lo que esta en backorder", "ref/web #/material num"}


# ---------------------------------------------------------------- carga de Open Orders
def load_open_orders(conn, data: bytes, name: str, fecha, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or fecha_nombre(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    check_order(conn, "openorders", file_date, file_hash)
    df = next(iter(read_table(data, name).values()))
    # Algunos archivos traen notas en el encabezado: "Remaining Qty (FALTA POR ENTREGRAR"
    df.columns = [re.split(r"\s*\(", str(c))[0].strip() for c in df.columns]
    require(df, OO_REQUIRED, "Open Orders")
    nuevas_cols = [c for c in df.columns if c.lower() not in OO_KNOWN and not c.lower().startswith("unnamed")]
    df = df[df["Sales Ord#"].map(lambda v: not _empty(v))]
    total = len(df)
    settings = get_settings(conn)
    wanted = {c.strip().lstrip("0") for c in settings.get("kgb_cust_ids", "").split(",") if c.strip()}
    if wanted and "Cust Num" in df.columns:
        df = df[df["Cust Num"].map(lambda v: (to_str(v) or "").lstrip("0")).isin(wanted)]
    if df.empty:
        raise LoadError("Ninguna línea del Open Orders corresponde al cliente configurado (Cliente KGB en Administración).")
    transito = int(settings.get("oo_transit_days", "22"))
    hoy = date.fromisoformat(file_date)
    prev = {(r["sales_ord"], r["item"]): dict(r) for r in conn.execute("SELECT sales_ord, item, first_seen FROM open_orders")}

    conn.defer("DELETE FROM open_orders")
    conn.defer("DELETE FROM open_order_history WHERE file_date=?", (file_date,))
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
            "req_date": to_date(r.get("Cust.Req.Date")), "part": part, "part_norm": norm_part(part),
            "description": to_str(r.get("Material Desc")), "ord_qty": to_num(r.get("Ord Qty")), "remaining_qty": qty,
            "scheduled_qty": to_num(r.get("Scheduled qty")), "status": status, "bloqueo": bloqueo, "recovery_raw": raw,
            "recovery_date": iso(rec), "salida_est": iso(salida), "llegada_est": iso(llegada),
            "first_seen": old["first_seen"] if old else file_date,
        }
        conn.defer(f"INSERT INTO open_orders({','.join(rec_row)}) VALUES({','.join('?' * len(rec_row))})", list(rec_row.values()))
        # El historial del día se borró arriba y (orden, ítem) no se repite: basta un INSERT.
        conn.defer("""INSERT INTO open_order_history(file_date, sales_ord, item, part, part_norm, remaining_qty,
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
    register_load(conn, "openorders", name, file_hash, file_date, user, len(seen), stats)
    audit(conn, user, "load_openorders", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- carga del KGB
def load_kgb(conn, data: bytes, name: str, fecha, user) -> dict:
    """Tránsito de Apple: guía, orden + ítem, país de destino y cuánto del KGB va a Colombia."""
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    check_order(conn, "kgb", file_date, file_hash)
    df = columns(next(iter(read_table(data, name).values())))
    require(df, KGB_REQUIRED, "KGB")

    wanted = {c.strip().lstrip("0") for c in get_settings(conn).get("kgb_cust_ids", "").split(",") if c.strip()}
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
    prev = conn.execute("SELECT TOP 1 file_date, summary FROM loads WHERE kind='kgb' ORDER BY file_date DESC, id DESC").fetchone()

    conn.defer("DELETE FROM kgb")
    for _, r in df.iterrows():
        prod = to_str(r["PROD_ID"])
        conn.defer(
            """INSERT INTO kgb(tracking, delivery_id, delivery_item, poso, poso_item, prod_id, prod_norm, prod_desc, status,
               current_event, event_dt, edd, pgi_dt, sla_ind, issue, qty, cust_id, ship_country)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["_trk"], to_str(r["DELIVERY_ID"]), to_str(r.get("DELIVERY_ITEM_NR")), to_str(r.get("POSO_NR")),
             to_str(r.get("POSO_ITEM_NR")), prod, norm_part(prod), to_str(r.get("PROD_DESC")), to_str(r["STATUS_DESC"]),
             to_str(r["CURRENT_EVENT"]), to_date(r.get("CURRENT_EVENT_DT")), to_date(r["EDD"]), to_date(r.get("PGI_DT")),
             to_str(r.get("IN_OUT_SLA_IND")), to_str(r.get("ISSUE_DESC")), float(r["_qty"]), r["_cust"],
             to_str(r.get("SHIP_TO_COUNTRY_CD"))))
    es661 = full["PROD_ID"].map(lambda v: norm_part(to_str(v)).startswith("661-"))
    transito = full["STATUS_DESC"].map(lambda v: (to_str(v) or "").lower() == "in transit")
    co_mask = full.index.isin(co.index)
    u = lambda m: float(full.loc[m, "_qty"].sum())
    stats = {"filas_archivo": int(total), "filas_cliente": int(len(df)), "guias": int(df["_trk"].dropna().nunique()),
             "unid_archivo": int(round(unid_total)), "filas_co": int(len(co)), "unid_co": int(round(co["_qty"].sum())),
             "guias_archivo": int(full["_trk"].dropna().nunique()), "guias_co": int(co["_trk"].dropna().nunique()),
             "pct_filas_co": pct(len(co), total), "pct_unid_co": pct(float(co["_qty"].sum()), unid_total),
             "unid_661": int(u(es661)), "unid_661_co": int(u(es661 & co_mask)), "pct_unid_661_co": pct(u(es661 & co_mask), u(es661)),
             "unid_transito": int(u(transito)), "unid_transito_co": int(u(transito & co_mask)),
             "pct_unid_transito_co": pct(u(transito & co_mask), u(transito)),
             "unid_co_consumibles": int(u(co_mask & ~es661))}
    if prev:
        p = json.loads(prev["summary"] or "{}")
        stats["fecha_ant"] = prev["file_date"]
        stats["pct_filas_co_ant"] = p.get("pct_filas_co", pct(p.get("filas_cliente", 0), p.get("filas_archivo", 0)))
        stats["pct_unid_co_ant"] = p.get("pct_unid_co")
    register_load(conn, "kgb", name, file_hash, file_date, user, int(len(df)), stats)
    audit(conn, user, "load_kgb", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


def kgb_historial(conn, n: int = 15) -> list:
    """Cuánto del KGB va a Colombia en cada carga y el cambio frente a la anterior (en puntos)."""
    out = []
    for r in conn.execute("SELECT file_date, summary FROM loads WHERE kind='kgb' ORDER BY file_date, id"):
        s = json.loads(r["summary"] or "{}")
        pf = s.get("pct_filas_co", pct(s.get("filas_cliente", 0), s.get("filas_archivo", 0)))
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
        "SELECT DISTINCT TOP 2 file_date FROM open_order_history ORDER BY file_date DESC")]
    if len(fechas) < 2:
        return {"desde": None, "hasta": fechas[0] if fechas else None, "lineas": [], "conteo": {}}
    hasta, desde = fechas
    oo_rows, kgb_rows, hist_rows = conn.select_many([
        ("SELECT sales_ord, item, part_norm FROM open_orders", ()),
        ("SELECT * FROM kgb", ()),
        ("SELECT * FROM open_order_history WHERE file_date=?", (desde,)),
    ])
    actual = {(r["sales_ord"], r["item"]) for r in oo_rows}
    actual_parte = {(r["sales_ord"], r["part_norm"]) for r in oo_rows}
    kgb_item = {(r["poso"], r["poso_item"]): r for r in kgb_rows if r["poso_item"] is not None}
    kgb_parte = {(r["poso"], r["prod_norm"]): r for r in kgb_rows}
    lineas, conteo = [], Counter()
    for r in hist_rows:
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


def _oferta(oo_rows, kgb_rows):
    oo = defaultdict(lambda: {"qty": 0.0, "lineas": 0, "ordenes": set(), "salida": None, "llegada": None,
                              "sin_fecha": 0.0, "estados": Counter(), "tipos": Counter(), "desc": None, "part": None})
    for r in oo_rows:
        a = oo[r["part_norm"]]
        a["qty"] += r["remaining_qty"] or 0
        a["lineas"] += 1
        a["ordenes"].add(r["sales_ord"])
        a["estados"][r["status"] or "—"] += r["remaining_qty"] or 0
        a["tipos"][r["tipo"]] += 1
        a["desc"] = a["desc"] or r["description"]
        a["part"] = a["part"] or r["part"]
        if r["salida_est"]:
            a["salida"] = min(a["salida"] or r["salida_est"], r["salida_est"])
            a["llegada"] = min(a["llegada"] or r["llegada_est"], r["llegada_est"])
        else:
            a["sin_fecha"] += r["remaining_qty"] or 0
    kg = defaultdict(lambda: {"con_guia": 0.0, "sin_guia": 0.0, "edd": None, "guias": set()})
    for r in kgb_rows:
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
    casos = [c for c in contexto.load_cases(conn, ctx) if c["pendiente"]]
    oo_rows, kgb_rows, oo_fecha, tot = conn.select_many([
        ("SELECT * FROM open_orders", ()),
        ("SELECT * FROM kgb", ()),
        ("SELECT MAX(file_date) m FROM loads WHERE kind='openorders'", ()),
        ("SELECT COUNT(*) n, COALESCE(SUM(remaining_qty),0) q, COUNT(DISTINCT sales_ord) o FROM open_orders", ()),
    ])
    oo, kg = _oferta(oo_rows, kgb_rows)
    salidas = _salidas(conn)
    sal_por_parte = Counter(x["part_norm"] for x in salidas["lineas"] if x["clave"] == "kgb")
    dem = defaultdict(lambda: {"casos": 0, "necesitan": 0, "miami": 0, "rojos": 0, "opciones": Counter(), "limite": None,
                               "parte": None, "desc": None})
    for c in casos:
        p = norm_part(c["assembly"])
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
        dm = dem.get(p) or {"casos": 0, "necesitan": 0, "miami": 0, "rojos": 0, "opciones": Counter(), "limite": None,
                            "parte": None, "desc": None}
        master = ctx.parts_norm.get(p) or {}
        sub = norm_part(master.get("substitute")) if master.get("substitute") else None
        grupo = [p] + ([sub] if sub and sub != p else [])
        o = _suma(grupo, oo, ("qty", "lineas", "salida", "llegada", "sin_fecha", "ordenes"))
        k = _suma(grupo, kg, ("con_guia", "sin_guia", "edd", "guias"))
        inv_qty = sum(ctx.inv_por_parte.get(x, 0) for x in grupo)
        estados = Counter()
        for x in grupo:
            if x in oo:
                estados.update(oo[x]["estados"])
        alertas = []
        if dm["necesitan"] and not o["qty"] and not (k["con_guia"] + k["sin_guia"]) and not inv_qty:
            compradas = dm.get("miami_compra", 0)
            if dm["necesitan"] > dm["miami"]:
                alertas.append({"code": "SIN_ABASTO", "level": "rojo",
                                "text": "Parte sin pedido ni tránsito: no está en Open Orders ni en el KGB."})
            elif compradas < dm["miami"]:
                alertas.append({"code": "SIN_ABASTO", "level": "ambar",
                                "text": f"Solo la trae la compra en Miami ({compradas} de {dm['miami']} casos con compra registrada)."})
            else:
                alertas.append({"code": "SIN_ABASTO", "level": "info",
                                "text": "Solo la trae Miami; " + ("el caso tiene compra registrada." if dm["miami"] == 1
                                                                  else f"los {dm['miami']} casos tienen compra registrada.")})
        if dm["necesitan"] and o["sin_fecha"] and not (k["con_guia"] + k["sin_guia"]):
            alertas.append({"code": "PEDIDO_SIN_FECHA", "level": "ambar",
                            "text": f"{int(o['sin_fecha'])} unid. pedidas sin Recovery ETA en Open Orders."})
        if dm["necesitan"] and o["llegada"] and dm["limite"] and o["llegada"] > dm["limite"] and not k["con_guia"]:
            alertas.append({"code": "PEDIDO_TARDE", "level": "info",
                            "text": f"Lo pedido llegaría hacia el {fmt(d(o['llegada']))}, después del límite más cercano ({fmt(d(dm['limite']))})."})
        nivel = "rojo" if any(a["level"] == "rojo" for a in alertas) else "ambar" if any(a["level"] == "ambar" for a in alertas) else "info" if alertas else "ok"
        filas.append({
            "parte": dm["parte"] or (oo[p]["part"] if p in oo else None) or p,
            "part_norm": p, "descripcion": dm["desc"] or master.get("description") or (oo[p]["desc"] if p in oo else None),
            "sustituto": master.get("substitute"), "casos": dm["casos"], "necesitan": dm["necesitan"], "rojos": dm["rojos"],
            "opciones": dict(dm["opciones"]), "limite": dm["limite"],
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
    tot = tot[0]
    return {
        "oo_fecha": oo_fecha[0]["m"] if oo_fecha else None, "kgb_fecha": ctx.kgb_date,
        "transito_dias": int(ctx.settings.get("oo_transit_days", "22")), "inv_fecha": ctx.inv_fecha,
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
    p = norm_part(part)
    master = ctx.parts_norm.get(p) or {}
    sub = norm_part(master.get("substitute")) if master.get("substitute") else None
    grupo = [p] + ([sub] if sub and sub != p else [])
    marcas = ",".join("?" * len(grupo))
    oo, kgb_rows, hist, abiertos = conn.select_many([
        (f"SELECT * FROM open_orders WHERE part_norm IN ({marcas}) ORDER BY COALESCE(salida_est, '9999'), sales_ord, item", grupo),
        (f"SELECT * FROM kgb WHERE prod_norm IN ({marcas}) ORDER BY COALESCE(edd, '9999')", grupo),
        (f"""SELECT TOP 10 file_date, SUM(remaining_qty) unidades, COUNT(*) lineas FROM open_order_history
             WHERE part_norm IN ({marcas}) GROUP BY file_date ORDER BY file_date DESC""", grupo),
        ("SELECT assembly FROM cases WHERE source_status='abierto' AND fecha_despacho IS NULL", ()),
    ])
    kgb = [{k: r[k] for k in ("tracking", "poso", "poso_item", "prod_id", "qty", "edd", "status", "current_event")}
           for r in kgb_rows]
    salidas = [x for x in _salidas(conn)["lineas"] if x["part_norm"] in grupo]
    casos = sum(1 for r in abiertos if norm_part(r["assembly"]) in grupo)
    return {"parte": part, "part_norm": p, "sustituto": master.get("substitute"), "casos_pendientes": casos,
            "open_orders": [dict(r) for r in oo], "kgb": kgb, "historia": [dict(r) for r in hist], "salidas": salidas,
            "transito_dias": int(ctx.settings.get("oo_transit_days", "22"))}
