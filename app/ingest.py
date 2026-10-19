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
            # La guía activa es la que la ACM escribe en Comments; la de Notes es la guía retrasada original.
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


# ---------------------------------------------------------------- precios y ship-to
def _find(df, *keys):
    for c in df.columns:
        low = c.lower()
        if any(k in low for k in keys):
            return c
    return None

PRICE_COLUMNS = {   # encabezado del archivo (en minúsculas) -> columna en la base
    "precio de inventario": "stocking", "stocking price": "stocking", "stocking": "stocking",
    "precio de intercambio": "exchange", "exchange price": "exchange", "exchange": "exchange",
    "precio de solo batería": "battery", "battery only price": "battery",
    "precio de solo pantalla": "display", "display only price": "display",
    "precio de la pantalla y el cristal de la parte trasera": "display_backglass",
    "display and back glass price": "display_backglass",
    "precio de solo cristal trasero": "backglass",
    "precio adicional": "addon", "add on price": "addon",
}
PART_HEADERS = ("row labels", "número de pieza", "numero de pieza", "part", "part number", "parte", "assembly", "material num", "prod_id")


def read_raw(data: bytes, name: str) -> pd.DataFrame:
    """Primera hoja sin encabezado, para archivos exportados de tablas dinámicas."""
    lower = name.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(io.BytesIO(data), sheet_name=0, header=None, engine="openpyxl", dtype=object)
        if lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(data), header=None, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as exc:
        raise LoadError(f"No se pudo leer el archivo: {exc.__class__.__name__}.") from exc
    raise LoadError("Formato no admitido. Use .xlsx o .csv.")


def load_prices(conn, data: bytes, name: str, user) -> dict:
    """Precios en COP por parte. Acepta la tabla dinámica 'Average of Precio' (encabezado en la fila 2)."""
    raw = read_raw(data, name)
    header_row = None
    for i in range(min(10, len(raw))):
        cells = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if any(c in PRICE_COLUMNS for c in cells):
            header_row = i
            break
    if header_row is None:
        raise LoadError("No encontré las columnas de precio (por ejemplo 'Precio de inventario' y 'Precio de intercambio').")
    headers = [str(v).strip().lower() for v in raw.iloc[header_row].tolist()]
    part_idx = next((i for i, h in enumerate(headers) if h in PART_HEADERS), 0)
    cols = {i: PRICE_COLUMNS[h] for i, h in enumerate(headers) if h in PRICE_COLUMNS}
    if "stocking" not in cols.values():
        raise LoadError("Falta la columna de precio de inventario (stocking).")

    def price(v):
        n = to_num(v)
        return n if n is not None and n > 1 else None    # "-", vacío y los marcadores de 1 no son precio

    conn.execute("DELETE FROM prices")
    n, now = 0, db.now_iso()
    for row in raw.iloc[header_row + 1:].itertuples(index=False):
        part = to_str(row[part_idx])
        if not part or part.lower() == "grand total":
            continue
        vals = {"stocking": None, "exchange": None, "battery": None, "display": None,
                "display_backglass": None, "backglass": None, "addon": None}
        for i, col in cols.items():
            vals[col] = price(row[i])
        if not any(vals.values()):
            continue
        part = part.upper()
        conn.execute(
            """INSERT OR REPLACE INTO prices(part, part_norm, stocking, exchange, battery, display, display_backglass,
               backglass, addon, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (part, logic.norm_part(part), vals["stocking"], vals["exchange"], vals["battery"], vals["display"],
             vals["display_backglass"], vals["backglass"], vals["addon"], now))
        n += 1
    con_exchange = conn.execute("SELECT COUNT(*) FROM prices WHERE exchange IS NOT NULL").fetchone()[0]
    stats = {"partes": n, "con_exchange": con_exchange, "columnas": sorted(set(cols.values()))}
    _register_load(conn, "precios", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, n, stats)
    db.audit(conn, user, "load_prices", name, stats)
    return stats


def load_parts(conn, data: bytes, name: str, user) -> dict:
    """Parts Data de Apple: descripción, producto y sustituto de cada parte.
    Los precios de este archivo no se usan (vienen del archivo de precios)."""
    import openpyxl
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise LoadError("El Parts Data debe ser un archivo .xlsx.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise LoadError(f"No se pudo leer el archivo: {exc.__class__.__name__}.") from exc
    rows = wb.worksheets[0].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, [])]
    idx = {h: i for i, h in enumerate(header)}
    missing = [c for c in ("Part Number", "Part Description", "Substitute Part") if c not in idx]
    if missing:
        raise LoadError(f"Al Parts Data le faltan columnas: {', '.join(missing)}.")
    get = lambda r, col: to_str(r[idx[col]]) if col in idx and idx[col] < len(r) else None
    parts = {}
    for r in rows:
        pn = get(r, "Part Number")
        if not pn:
            continue
        pn = pn.upper()
        rec = parts.setdefault(pn, {"description": get(r, "Part Description"), "product": get(r, "Product Name"),
                                    "group": get(r, "Component Group"), "substitute": None})
        sub = get(r, "Substitute Part")
        if sub:
            rec["substitute"] = sub.upper()
    wb.close()
    if not parts:
        raise LoadError("El Parts Data no tiene partes.")
    now = db.now_iso()
    conn.execute("DELETE FROM parts")
    conn.executemany(
        "INSERT INTO parts(part, part_norm, description, product, component_group, substitute, updated_at) VALUES(?,?,?,?,?,?,?)",
        [(pn, logic.norm_part(pn), v["description"], v["product"], v["group"], v["substitute"], now) for pn, v in parts.items()])
    stats = {"partes": len(parts), "con_sustituto": sum(1 for v in parts.values() if v["substitute"])}
    _register_load(conn, "partes", name, hashlib.sha256(data).hexdigest(), date.today().isoformat(), user, len(parts), stats)
    db.audit(conn, user, "load_parts", name, stats)
    return stats


def _pick(df, exact, *contains):
    for c in df.columns:
        if c.lower() in exact:
            return c
    return _find(df, *contains) if contains else None


def load_shipto(conn, data: bytes, name: str, user) -> dict:
    """Tiendas (AASP): nombre, ciudad, correo principal (Service manager) y copia (Coordinador).
    La dirección no se guarda porque los correos solo llevan tiempos."""
    df = _columns(next(iter(read_table(data, name).values())))
    id_col = _pick(df, ("shipto", "ship_to", "ship-to"), "ship")
    if not id_col:
        raise LoadError("El archivo de tiendas necesita una columna 'shipto'.")
    name_col = _pick(df, ("shipto_name", "nombre", "tienda"), "name", "nombre")
    mail_col = _pick(df, ("service manager", "correo", "email"), "manager", "correo", "mail")
    cc_col = _pick(df, ("coordinador", "cc", "copia"), "coordinador", "copia")
    city_col = _pick(df, ("city", "ciudad"), "ciudad", "city")
    active_col = _pick(df, ("active", "activo"))
    n, skipped, now = 0, 0, db.now_iso()
    for _, r in df.iterrows():
        sid = to_str(r[id_col])
        if not sid:
            continue
        if active_col and (to_str(r[active_col]) or "Y").upper() in ("N", "NO", "FALSE", "0"):
            skipped += 1
            continue
        old = conn.execute("SELECT * FROM shipto WHERE ship_to=?", (sid,)).fetchone()
        old = dict(old) if old else {}
        email = (to_str(r[mail_col]) if mail_col else None) or old.get("email")
        cc = (to_str(r[cc_col]) if cc_col else None) or old.get("cc_email")
        cliente = email.split("@")[-1].lower() if email and "@" in email else old.get("cliente")
        conn.execute(
            "INSERT OR REPLACE INTO shipto(ship_to, name, email, cc_email, cliente, city, updated_at) VALUES(?,?,?,?,?,?,?)",
            (sid, (to_str(r[name_col]) if name_col else None) or old.get("name"), email, cc, cliente,
             (to_str(r[city_col]) if city_col else None) or old.get("city"), now))
        n += 1
    stats = {"tiendas": n, "inactivas_omitidas": skipped, "con_correo": bool(mail_col), "con_copia": bool(cc_col)}
    _register_load(conn, "shipto", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, n, stats)
    db.audit(conn, user, "load_shipto", name, stats)
    return stats