"""Carga de archivos: backlog diario, KGB, precios y ship-to.

Regla de oro del backlog: lo que viene de Apple se actualiza; lo que registra el equipo nunca se sobrescribe.
"""
import hashlib
import io
import json
import math
import re
from datetime import date, datetime

import pandas as pd

from . import db, logic


class LoadError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


BACKLOG_REQUIRED = ["Key_Notif", "Notifctn", "Line_item", "Assembly", "Description_Assembly", "Created_on",
                    "PO_Number", "Ship_to", "List_Name", "Warranty_coverage", "Report_date", "Repair Strategy",
                    "Option", "Initial ETA Resolution", "Comments", "Notes"]
KGB_REQUIRED = ["TRACKING_NR", "PROD_ID", "EDD", "CUST_ID", "STATUS_DESC", "CURRENT_EVENT", "DELIVERY_ID", "DELIVERY_QTY"]
JENNY_FIELDS = [("option_jenny", "Option"), ("eta_jenny", "ETA"), ("comments_jenny", "Comments"), ("notes_jenny", "Notes")]


# ---------------------------------------------------------------- utilidades
def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip()) or v is pd.NaT


def to_str(v):
    if _empty(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def to_date(v):
    if _empty(v):
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return pd.to_datetime(str(v), dayfirst=False).date().isoformat()
    except (ValueError, TypeError):
        return None


def to_num(v):
    if _empty(v):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def date_from_name(name: str):
    m = re.search(r"(20\d{2})[_\-.](\d{2})[_\-.](\d{2})", name)          # 2026_09_28
    if m:
        y, mo, da = m.groups()
    else:
        m = re.search(r"(\d{2})[_\-.](\d{2})[_\-.](20\d{2})", name)      # 09_29_2026
        if not m:
            return None
        mo, da, y = m.groups()
    try:
        return date(int(y), int(mo), int(da)).isoformat()
    except ValueError:
        return None


def read_table(data: bytes, name: str) -> dict:
    """Devuelve {hoja: DataFrame}. Soporta xlsx/xlsm y csv."""
    lower = name.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl", dtype=object)
        if lower.endswith(".csv"):
            return {"csv": pd.read_csv(io.BytesIO(data), dtype=str, sep=None, engine="python", encoding="utf-8-sig")}
    except Exception as exc:  # archivo dañado o con formato inesperado
        raise LoadError(f"No se pudo leer el archivo: {exc.__class__.__name__}.") from exc
    raise LoadError("Formato no admitido. Use .xlsx o .csv.")


def _columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _require(df, required, what):
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise LoadError(f"Al archivo de {what} le faltan columnas: {', '.join(missing)}.")


def _check_order(conn, kind, file_date, file_hash):
    if conn.execute("SELECT 1 FROM loads WHERE kind=? AND file_hash=?", (kind, file_hash)).fetchone():
        raise LoadError("Este archivo ya se había cargado.", 409)
    last = conn.execute("SELECT MAX(file_date) m FROM loads WHERE kind=?", (kind,)).fetchone()["m"]
    if last and file_date < last:
        raise LoadError(f"El archivo es del {file_date} y ya hay uno cargado del {last}. Cargue los archivos en orden.", 409)


def _register_load(conn, kind, name, file_hash, file_date, user, rows, summary):
    cur = conn.execute(
        "INSERT INTO loads(kind, file_name, file_hash, file_date, loaded_at, user_id, rows, summary) VALUES(?,?,?,?,?,?,?,?)",
        (kind, name, file_hash, file_date, db.now_iso(), user["id"] if user else None, rows,
         json.dumps(summary, ensure_ascii=False)))
    return cur.lastrowid


# ---------------------------------------------------------------- backlog
def load_backlog(conn, data: bytes, name: str, fecha: str | None, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    _check_order(conn, "backlog", file_date, file_hash)

    sheets = {k.strip().lower(): _columns(v) for k, v in read_table(data, name).items()}
    df = sheets.get("backlog")
    if df is None and len(sheets) == 1:
        df = next(iter(sheets.values()))
    if df is None:
        raise LoadError("No encontré la hoja 'Backlog'.")
    _require(df, BACKLOG_REQUIRED, "backlog")
    df = df[df["Key_Notif"].map(lambda v: not _empty(v))]
    allocated = set()
    if "allocated" in sheets and "Key_Notif" in sheets["allocated"].columns:
        allocated = {to_str(v) for v in sheets["allocated"]["Key_Notif"] if not _empty(v)}

    existing = {r["key_notif"]: dict(r) for r in conn.execute("SELECT * FROM cases")}
    stats = {"nuevas": 0, "actualizadas": 0, "cambios_eta": 0, "cambios_opcion": 0, "cerradas": 0,
             "asignadas": 0, "reabiertas": 0, "filas": int(len(df))}
    load_id = _register_load(conn, "backlog", name, file_hash, file_date, user, int(len(df)), {})
    seen = set()
    now = db.now_iso()

    for _, r in df.iterrows():
        key = to_str(r["Key_Notif"])
        if key in seen:
            continue
        seen.add(key)
        comments, notes = to_str(r["Comments"]), to_str(r["Notes"])
        assembly = to_str(r["Assembly"])
        rec = {
            "notifctn": to_str(r["Notifctn"]), "line_item": int(to_num(r["Line_item"]) or 0), "assembly": assembly,
            "description": to_str(r["Description_Assembly"]), "created_on": to_date(r["Created_on"]),
            "po_number": to_str(r["PO_Number"]), "ship_to": to_str(r["Ship_to"]), "list_name": to_str(r["List_Name"]),
            "material_desc": to_str(r.get("Material_desc")), "serial_no": to_str(r.get("Serial_No")),
            "warranty": to_str(r["Warranty_coverage"]), "report_date": to_date(r["Report_date"]),
            "product_family": to_str(r.get("Product Family")), "repair_strategy": to_str(r["Repair Strategy"]),
            "product_description": to_str(r.get("Product Description")),
            "option_jenny": to_str(r["Option"]), "eta_jenny": to_date(r["Initial ETA Resolution"]),
            "comments_jenny": comments, "notes_jenny": notes,
            # La guía activa es la que Jenny escribe en Comments; la de Notes es la guía retrasada original.
            "tracking": json.dumps(logic.parse_tracking(comments)),
            "subs": json.dumps(logic.parse_parts(comments, exclude=assembly)),
        }
        conn.execute("INSERT OR REPLACE INTO snapshots(file_date, key_notif, option_jenny, eta_jenny) VALUES(?,?,?,?)",
                     (file_date, key, rec["option_jenny"], rec["eta_jenny"]))
        old = existing.get(key)
        if old is None:
            cols = list(rec) + ["key_notif", "eta_first", "first_seen", "last_seen", "oh_since", "updated_at"]
            vals = list(rec.values()) + [key, rec["eta_jenny"], file_date, file_date,
                                         file_date if rec["option_jenny"] == "OH" else None, now]
            conn.execute(f"INSERT INTO cases({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            stats["nuevas"] += 1
            continue

        extra = {"last_seen": file_date, "updated_at": now}
        for field, label in JENNY_FIELDS:
            if (old[field] or None) != (rec[field] or None):
                conn.execute("INSERT INTO case_history(key_notif, load_id, file_date, field, old, new) VALUES(?,?,?,?,?,?)",
                             (key, load_id, file_date, label, old[field], rec[field]))
                if field == "eta_jenny" and old[field] and rec[field]:
                    extra["eta_changes"] = old["eta_changes"] + 1
                    stats["cambios_eta"] += 1
                if field == "option_jenny":
                    stats["cambios_opcion"] += 1
        if not old["eta_first"] and rec["eta_jenny"]:
            extra["eta_first"] = rec["eta_jenny"]
        if rec["option_jenny"] == "OH" and old["option_jenny"] != "OH":
            extra["oh_since"] = file_date
        elif rec["option_jenny"] != "OH":
            extra["oh_since"] = None
        if old["source_status"] == "cerrado":
            extra.update(source_status="abierto", closed_on=None, close_reason=None, reopened=1)
            conn.execute("INSERT INTO case_history(key_notif, load_id, file_date, field, old, new) VALUES(?,?,?,?,?,?)",
                         (key, load_id, file_date, "Estado del reporte", "cerrado", "reabierto"))
            stats["reabiertas"] += 1
        upd = {**rec, **extra}
        conn.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in upd)} WHERE key_notif=?", [*upd.values(), key])
        stats["actualizadas"] += 1

    for key, old in existing.items():
        if old["source_status"] == "abierto" and key not in seen:
            reason = "Asignado" if key in allocated else "Salió del reporte"
            conn.execute("UPDATE cases SET source_status='cerrado', closed_on=?, close_reason=?, updated_at=? WHERE key_notif=?",
                         (file_date, reason, now, key))
            conn.execute("INSERT INTO case_history(key_notif, load_id, file_date, field, old, new) VALUES(?,?,?,?,?,?)",
                         (key, load_id, file_date, "Estado del reporte", "abierto", f"cerrado ({reason})"))
            stats["cerradas"] += 1
            stats["asignadas"] += reason == "Asignado"

    stats["abiertas"] = conn.execute("SELECT COUNT(*) n FROM cases WHERE source_status='abierto'").fetchone()["n"]
    conn.execute("UPDATE loads SET summary=? WHERE id=?", (json.dumps(stats), load_id))
    db.audit(conn, user, "load_backlog", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- KGB
def load_kgb(conn, data: bytes, name: str, fecha: str | None, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    _check_order(conn, "kgb", file_date, file_hash)
    sheets = read_table(data, name)
    df = _columns(next(iter(sheets.values())))
    _require(df, KGB_REQUIRED, "KGB")

    wanted = {c.strip().lstrip("0") for c in db.get_settings(conn).get("kgb_cust_ids", "").split(",") if c.strip()}
    df["_cust"] = df["CUST_ID"].map(lambda v: (to_str(v) or "").lstrip("0"))
    total = len(df)
    if wanted:
        df = df[df["_cust"].isin(wanted)]
    if df.empty:
        raise LoadError("Ninguna fila del KGB corresponde al cliente configurado. Revise 'Cliente KGB' en Administración.")

    conn.execute("DELETE FROM kgb")
    for _, r in df.iterrows():
        prod = to_str(r["PROD_ID"])
        conn.execute(
            """INSERT INTO kgb(tracking, delivery_id, delivery_item, poso, prod_id, prod_norm, prod_desc, status,
               current_event, event_dt, edd, pgi_dt, sla_ind, issue, qty, cust_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (to_str(r["TRACKING_NR"]), to_str(r["DELIVERY_ID"]), to_str(r.get("DELIVERY_ITEM_NR")), to_str(r.get("POSO_NR")),
             prod, logic.norm_part(prod), to_str(r.get("PROD_DESC")), to_str(r["STATUS_DESC"]),
             to_str(r["CURRENT_EVENT"]), to_date(r.get("CURRENT_EVENT_DT")), to_date(r["EDD"]), to_date(r.get("PGI_DT")),
             to_str(r.get("IN_OUT_SLA_IND")), to_str(r.get("ISSUE_DESC")), to_num(r["DELIVERY_QTY"]), r["_cust"]))
    guides = df["TRACKING_NR"].map(to_str).dropna().nunique()
    stats = {"filas_archivo": int(total), "filas_cliente": int(len(df)), "guias": int(guides)}
    _register_load(conn, "kgb", name, file_hash, file_date, user, int(len(df)), stats)
    db.audit(conn, user, "load_kgb", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- precios y ship-to
def _find(df, *keys):
    for c in df.columns:
        low = c.lower()
        if any(k in low for k in keys):
            return c
    return None


def load_prices(conn, data: bytes, name: str, user) -> dict:
    df = _columns(next(iter(read_table(data, name).values())))
    part_col = next((c for c in df.columns if c.lower() in
                     ("assembly", "part", "part number", "part_number", "material", "material num", "prod_id", "parte")), None) \
        or _find(df, "part", "material", "assembly")
    stock_col, exch_col = _find(df, "stock"), _find(df, "exch")
    desc_col = _find(df, "desc")
    if not part_col or not (stock_col or exch_col):
        raise LoadError("El archivo de precios necesita una columna de parte y al menos una de 'stocking' o 'exchange'.")
    conn.execute("DELETE FROM prices")
    n = 0
    now = db.now_iso()
    for _, r in df.iterrows():
        part = to_str(r[part_col])
        if not part:
            continue
        conn.execute("INSERT OR REPLACE INTO prices(part, part_norm, description, stocking, exchange, updated_at) VALUES(?,?,?,?,?,?)",
                     (part.upper(), logic.norm_part(part), to_str(r[desc_col]) if desc_col else None,
                      to_num(r[stock_col]) if stock_col else None, to_num(r[exch_col]) if exch_col else None, now))
        n += 1
    stats = {"partes": n, "columna_parte": part_col, "stocking": stock_col, "exchange": exch_col}
    _register_load(conn, "precios", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, n, stats)
    db.audit(conn, user, "load_prices", name, stats)
    return stats


def load_shipto(conn, data: bytes, name: str, user) -> dict:
    df = _columns(next(iter(read_table(data, name).values())))
    id_col = _find(df, "ship")
    if not id_col:
        raise LoadError("El archivo de tiendas necesita una columna 'Ship_to'.")
    name_col = _find(df, "nombre", "name", "tienda")
    mail_col = _find(df, "correo", "email", "mail")
    city_col = _find(df, "ciudad", "city")
    n, now = 0, db.now_iso()
    for _, r in df.iterrows():
        sid = to_str(r[id_col])
        if not sid:
            continue
        old = conn.execute("SELECT * FROM shipto WHERE ship_to=?", (sid,)).fetchone()
        old = dict(old) if old else {}
        conn.execute("INSERT OR REPLACE INTO shipto(ship_to, name, email, city, updated_at) VALUES(?,?,?,?,?)",
                     (sid, (to_str(r[name_col]) if name_col else None) or old.get("name"),
                      (to_str(r[mail_col]) if mail_col else None) or old.get("email"),
                      (to_str(r[city_col]) if city_col else None) or old.get("city"), now))
        n += 1
    stats = {"tiendas": n, "con_correo": bool(mail_col)}
    _register_load(conn, "shipto", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, n, stats)
    db.audit(conn, user, "load_shipto", name, stats)
    return stats
