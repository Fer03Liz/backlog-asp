"""Carga de archivos: backlog diario, precios, Parts Data y tiendas (ship-to).

Regla de oro del backlog: lo que viene de Apple se actualiza; lo que registra el equipo nunca se sobrescribe.
Las escrituras fila por fila se acumulan (`defer`) y viajan en lotes: el servidor está lejos.
"""
import hashlib
import io
import json
from datetime import date

from ..domain.abastecimiento import date_from_name
from ..domain.errores import LoadError
from ..domain.partes import norm_part, parse_parts, parse_tracking
from .archivos import _empty, columns, pick, read_raw, read_table, require, to_date, to_num, to_str
from .auditoria import audit, now_iso

BACKLOG_REQUIRED = ["Key_Notif", "Notifctn", "Line_item", "Assembly", "Description_Assembly", "Created_on",
                    "PO_Number", "Ship_to", "List_Name", "Warranty_coverage", "Report_date", "Repair Strategy",
                    "Option", "Initial ETA Resolution", "Comments", "Notes"]
KGB_REQUIRED = ["TRACKING_NR", "PROD_ID", "EDD", "CUST_ID", "STATUS_DESC", "CURRENT_EVENT", "DELIVERY_ID", "DELIVERY_QTY"]
JENNY_FIELDS = [("option_jenny", "Option"), ("eta_jenny", "ETA"), ("comments_jenny", "Comments"), ("notes_jenny", "Notes")]
_HIST = "INSERT INTO case_history(key_notif, load_id, file_date, field, old, new) VALUES(?,?,?,?,?,?)"


def check_order(conn, kind, file_date, file_hash):
    dup, last = conn.select_many([
        ("SELECT 1 x FROM loads WHERE kind=? AND file_hash=?", (kind, file_hash)),
        ("SELECT MAX(file_date) m FROM loads WHERE kind=?", (kind,)),
    ])
    if dup:
        raise LoadError("Este archivo ya se había cargado.", 409)
    last = last[0]["m"] if last else None
    if last and file_date < last:
        raise LoadError(f"El archivo es del {file_date} y ya hay uno cargado del {last}. Cargue los archivos en orden.", 409)


def register_load(conn, kind, name, file_hash, file_date, user, rows, summary):
    cur = conn.execute(
        "INSERT INTO loads(kind, file_name, file_hash, file_date, loaded_at, user_id, [rows], summary) VALUES(?,?,?,?,?,?,?,?)",
        (kind, name, file_hash, file_date, now_iso(), user["id"] if user else None, rows,
         json.dumps(summary, ensure_ascii=False)))
    return cur.lastrowid


# ---------------------------------------------------------------- backlog
def load_backlog(conn, data: bytes, name: str, fecha: str | None, user) -> dict:
    file_hash = hashlib.sha256(data).hexdigest()
    file_date = fecha or date_from_name(name)
    if not file_date:
        raise LoadError("No encontré la fecha en el nombre del archivo. Indíquela en el formulario.")
    check_order(conn, "backlog", file_date, file_hash)

    sheets = {k.strip().lower(): columns(v) for k, v in read_table(data, name).items()}
    df = sheets.get("backlog")
    if df is None and len(sheets) == 1:
        df = next(iter(sheets.values()))
    if df is None:
        raise LoadError("No encontré la hoja 'Backlog'.")
    require(df, BACKLOG_REQUIRED, "backlog")
    df = df[df["Key_Notif"].map(lambda v: not _empty(v))]
    allocated = set()
    if "allocated" in sheets and "Key_Notif" in sheets["allocated"].columns:
        allocated = {to_str(v) for v in sheets["allocated"]["Key_Notif"] if not _empty(v)}

    existing = {r["key_notif"]: dict(r) for r in conn.execute("SELECT * FROM cases")}
    stats = {"nuevas": 0, "actualizadas": 0, "cambios_eta": 0, "cambios_opcion": 0, "cerradas": 0,
             "asignadas": 0, "reabiertas": 0, "filas": int(len(df))}
    load_id = register_load(conn, "backlog", name, file_hash, file_date, user, int(len(df)), {})
    seen = set()
    now = now_iso()

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
            "tracking": json.dumps(parse_tracking(comments)),
            "subs": json.dumps(parse_parts(comments, exclude=assembly)),
        }
        conn.upsert("snapshots", {"file_date": file_date, "key_notif": key, "option_jenny": rec["option_jenny"],
                                  "eta_jenny": rec["eta_jenny"]}, ("file_date", "key_notif"), defer=True)
        old = existing.get(key)
        if old is None:
            cols = list(rec) + ["key_notif", "eta_first", "first_seen", "last_seen", "oh_since", "updated_at"]
            vals = list(rec.values()) + [key, rec["eta_jenny"], file_date, file_date,
                                         file_date if rec["option_jenny"] == "OH" else None, now]
            conn.defer(f"INSERT INTO cases({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            stats["nuevas"] += 1
            continue

        extra = {"last_seen": file_date, "updated_at": now}
        for field, label in JENNY_FIELDS:
            if (old[field] or None) != (rec[field] or None):
                conn.defer(_HIST, (key, load_id, file_date, label, old[field], rec[field]))
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
            conn.defer(_HIST, (key, load_id, file_date, "Estado del reporte", "cerrado", "reabierto"))
            stats["reabiertas"] += 1
        upd = {**rec, **extra}
        conn.defer(f"UPDATE cases SET {', '.join(f'{k}=?' for k in upd)} WHERE key_notif=?", [*upd.values(), key])
        stats["actualizadas"] += 1

    for key, old in existing.items():
        if old["source_status"] == "abierto" and key not in seen:
            reason = "Asignado" if key in allocated else "Salió del reporte"
            conn.defer("UPDATE cases SET source_status='cerrado', closed_on=?, close_reason=?, updated_at=? WHERE key_notif=?",
                       (file_date, reason, now, key))
            conn.defer(_HIST, (key, load_id, file_date, "Estado del reporte", "abierto", f"cerrado ({reason})"))
            stats["cerradas"] += 1
            stats["asignadas"] += reason == "Asignado"

    stats["abiertas"] = conn.execute("SELECT COUNT(*) n FROM cases WHERE source_status='abierto'").fetchone()["n"]
    conn.defer("UPDATE loads SET summary=? WHERE id=?", (json.dumps(stats), load_id))
    audit(conn, user, "load_backlog", name, {"fecha": file_date, **stats})
    return {"fecha": file_date, **stats}


# ---------------------------------------------------------------- precios, Parts Data y ship-to
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

    precios, now = {}, now_iso()                         # si una parte se repite, queda la última fila
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
        precios[part.upper()] = vals
    conn.execute("DELETE FROM prices")
    conn.executemany(
        """INSERT INTO prices(part, part_norm, stocking, exchange, battery, display, display_backglass,
           backglass, addon, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [(part, norm_part(part), v["stocking"], v["exchange"], v["battery"], v["display"], v["display_backglass"],
          v["backglass"], v["addon"], now) for part, v in precios.items()])
    stats = {"partes": len(precios), "con_exchange": sum(1 for v in precios.values() if v["exchange"] is not None),
             "columnas": sorted(set(cols.values()))}
    register_load(conn, "precios", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, len(precios), stats)
    audit(conn, user, "load_prices", name, stats)
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
    now = now_iso()
    conn.execute("DELETE FROM parts")
    conn.executemany(
        "INSERT INTO parts(part, part_norm, description, product, component_group, substitute, updated_at) VALUES(?,?,?,?,?,?,?)",
        [(pn, norm_part(pn), v["description"], v["product"], v["group"], v["substitute"], now) for pn, v in parts.items()])
    stats = {"partes": len(parts), "con_sustituto": sum(1 for v in parts.values() if v["substitute"])}
    register_load(conn, "partes", name, hashlib.sha256(data).hexdigest(), date.today().isoformat(), user, len(parts), stats)
    audit(conn, user, "load_parts", name, stats)
    return stats


def load_shipto(conn, data: bytes, name: str, user) -> dict:
    """Tiendas (AASP): nombre, ciudad, correo principal (Service manager) y copia (Coordinador).
    La dirección no se guarda porque los correos solo llevan tiempos."""
    df = columns(next(iter(read_table(data, name).values())))
    id_col = pick(df, ("shipto", "ship_to", "ship-to"), "ship")
    if not id_col:
        raise LoadError("El archivo de tiendas necesita una columna 'shipto'.")
    name_col = pick(df, ("shipto_name", "nombre", "tienda"), "name", "nombre")
    mail_col = pick(df, ("service manager", "correo", "email"), "manager", "correo", "mail")
    cc_col = pick(df, ("coordinador", "cc", "copia"), "coordinador", "copia")
    city_col = pick(df, ("city", "ciudad"), "ciudad", "city")
    active_col = pick(df, ("active", "activo"))
    actuales = {r["ship_to"]: dict(r) for r in conn.execute("SELECT * FROM shipto")}
    n, skipped, now = 0, 0, now_iso()
    for _, r in df.iterrows():
        sid = to_str(r[id_col])
        if not sid:
            continue
        if active_col and (to_str(r[active_col]) or "Y").upper() in ("N", "NO", "FALSE", "0"):
            skipped += 1
            continue
        old = actuales.get(sid, {})
        email = (to_str(r[mail_col]) if mail_col else None) or old.get("email")
        cc = (to_str(r[cc_col]) if cc_col else None) or old.get("cc_email")
        cliente = email.split("@")[-1].lower() if email and "@" in email else old.get("cliente")
        fila = {"ship_to": sid, "name": (to_str(r[name_col]) if name_col else None) or old.get("name"), "email": email,
                "cc_email": cc, "cliente": cliente, "city": (to_str(r[city_col]) if city_col else None) or old.get("city"),
                "updated_at": now}
        conn.upsert("shipto", fila, ("ship_to",), defer=True)
        actuales[sid] = fila
        n += 1
    stats = {"tiendas": n, "inactivas_omitidas": skipped, "con_correo": bool(mail_col), "con_copia": bool(cc_col)}
    register_load(conn, "shipto", name, hashlib.sha256(data).hexdigest() + now, date.today().isoformat(), user, n, stats)
    audit(conn, user, "load_shipto", name, stats)
    return stats
