"""Consultas y acciones sobre casos y guías: detalle, comentarios, guías, llegada de guías y exportación."""
import csv
import io
import json

from ..domain.casos import enrich
from ..domain.errores import ErrorNegocio
from ..domain.gestion import recepcion
from ..domain.partes import norm_part
from . import acceso, contexto, gestion
from .auditoria import audit, now_iso


def detalle(conn, user, key: str) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not row:
        raise ErrorNegocio("Caso no encontrado.", 404)
    ctx = contexto.Context(conn)
    case = enrich(dict(row), ctx)
    pnorm = norm_part(case["assembly"])
    guide_rows = [r for tk in case["tracking_list"] for r in ctx.kgb_by_trk.get(tk, [])]
    rel = {pnorm} | {norm_part(s) for s in case["subs_list"]}
    historial, auditoria = conn.select_many([
        ("SELECT file_date, field, old, new FROM case_history WHERE key_notif=? ORDER BY id DESC", (key,)),
        ("SELECT TOP 50 at, username, detail FROM audit_log WHERE target=? AND action='case_update' ORDER BY id DESC", (key,)),
    ])
    return {
        "caso": acceso.caso(user, case),
        "historial": [dict(r) for r in historial],
        "comentarios": acceso.comentarios(conn, user, key),
        "solicitudes": gestion.solicitudes(conn, key=key) if user["role"] in acceso.INTERNOS else [],
        "gestion": acceso.gestion(user, [dict(r) for r in auditoria]),
        "kgb_guia": [{k: r[k] for k in ("tracking", "prod_id", "prod_desc", "qty", "edd", "current_event", "sla_ind", "status")}
                     for r in guide_rows if r["prod_norm"] in rel],
        "kgb_guia_total": len(guide_rows),
        "kgb_parte": ctx.kgb_by_part.get(pnorm) and {**ctx.kgb_by_part[pnorm], "guides": sorted(ctx.kgb_by_part[pnorm]["guides"])},
    }


def comentar(conn, user, key: str, text: str, visibilidad: str) -> None:
    if not conn.execute("SELECT 1 x FROM cases WHERE key_notif=?", (key,)).fetchone():
        raise ErrorNegocio("Caso no encontrado.", 404)
    text = text.strip()
    if not text:
        raise ErrorNegocio("El comentario está vacío.", 422)
    visibilidad = "compartido" if user["role"] == "acm" else visibilidad
    if visibilidad not in ("interno", "compartido"):
        raise ErrorNegocio("Visibilidad no válida.", 422)
    conn.defer("INSERT INTO comments(key_notif, user_id, username, at, text, visibilidad) VALUES(?,?,?,?,?,?)",
               (key, user["id"], user["username"], now_iso(), text, visibilidad))
    audit(conn, user, "comment", key, {"texto": text[:200], "visibilidad": visibilidad})


# ---------------------------------------------------------------- guías
def guias(conn) -> dict:
    ctx = contexto.Context(conn)
    agg = {}
    for c in contexto.load_cases(conn, ctx):
        for tk in c["tracking_list"]:
            g = agg.setdefault(tk, {"guia": tk, "casos": 0, "pendientes": 0, "partes": set(), "tiendas": set(),
                                    "claves": [], "eta_min": None, "retrasada": False, "rojas": 0})
            g["casos"] += 1
            g["claves"].append(c["key_notif"])
            g["pendientes"] += c["pendiente"]
            g["partes"].add(c["assembly"])
            g["tiendas"].add(c["list_name"])
            g["rojas"] += c["nivel"] == "rojo"
            notes = (c["notes_jenny"] or "").lower()
            g["retrasada"] |= "delayed" in notes and tk.lower() in notes
            if c["eta_jenny"] and (g["eta_min"] is None or c["eta_jenny"] < g["eta_min"]):
                g["eta_min"] = c["eta_jenny"]
    out = []
    for tk, g in agg.items():
        rows = ctx.kgb_by_trk.get(tk, [])
        edds = sorted(r["edd"] for r in rows if r["edd"])
        g["retrasada"] |= any((r["current_event"] or "").lower().startswith("shipment delay") or r["issue"] for r in rows)
        g["fuera_sla"] = any("out" in (r["sla_ind"] or "").lower() for r in rows)
        if edds and g["eta_min"] and edds[0] > g["eta_min"]:
            g["retrasada"] = True             # la llegada del KGB pasa la ETA de Apple más cercana
        texto = " ".join([tk, *g["claves"], *g["partes"], *(x or "" for x in g["tiendas"])]).lower()
        out.append({**g, "partes": len(g["partes"]), "tiendas": len(g["tiendas"]), "buscar": texto, "en_kgb": bool(rows),
                    "kgb_lineas": len(rows), "kgb_edd": edds[0] if edds else None,
                    "kgb_evento": next((r["current_event"] for r in rows if r["current_event"]), None),
                    "llegada": ctx.arrivals.get(tk)})
    out.sort(key=lambda g: (g["llegada"] is not None, g["kgb_edd"] or g["eta_min"] or "9999"))
    return {"kgb_fecha": ctx.kgb_date, "guias": out}


def llegada_guia(conn, user, tracking: str, fecha: str) -> dict:
    rows, previa = conn.select_many([
        ("SELECT key_notif, tracking, estado, fecha_recepcion FROM cases WHERE source_status='abierto' AND fecha_despacho IS NULL", ()),
        ("SELECT arrived_on FROM guide_arrivals WHERE tracking=?", (tracking,)),
    ])
    keys = [dict(r) for r in rows if tracking in json.loads(r["tracking"] or "[]")]
    if not keys:
        raise ErrorNegocio("No hay casos abiertos con esa guía.", 404)
    previa = previa[0] if previa else None
    if previa and previa["arrived_on"] != fecha and user["role"] != "admin":
        raise ErrorNegocio(f"La llegada de esta guía ya está registrada ({previa['arrived_on']}). "
                           "Para cambiarla, pídalo a un administrador con el motivo.", 403)
    conn.upsert("guide_arrivals", {"tracking": tracking, "arrived_on": fecha, "user_id": user["id"], "at": now_iso()},
                ("tracking",), defer=True)
    updated = 0
    for r in keys:
        diff = recepcion(r, fecha)
        if diff:
            sets = {k: v["ahora"] for k, v in diff.items()}
            conn.defer(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE key_notif=?",
                       [*sets.values(), now_iso(), r["key_notif"]])
            audit(conn, user, "case_update", r["key_notif"], {**diff, "origen": f"llegada de guía {tracking}"})
            updated += 1
    audit(conn, user, "guide_arrival", tracking, {"fecha": fecha, "casos": len(keys)})
    return {"casos": len(keys), "actualizados": updated}


# ---------------------------------------------------------------- exportación
EXPORT_COLS = ["key_notif", "list_name", "ship_to", "assembly", "description", "option_jenny", "eta_jenny", "eta_first",
               "eta_changes", "llegada", "llegada_fuente", "despacho_posible", "limite_fr", "edad", "estado", "responsable",
               "fecha_recepcion", "fecha_despacho", "fecha_comunicada", "aprobacion_kam", "warranty", "tipo_precio", "precio",
               "repair_strategy", "kgb_match", "kgb_edd", "nivel", "source_status", "closed_on", "close_reason"]


def _cell(v):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def exportar_csv(conn, user, ctx) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLS + ["alertas"])
    for c in contexto.load_cases(conn, ctx, include_closed=True, closed_days=3650):
        w.writerow([_cell(c.get(k)) for k in EXPORT_COLS] + [_cell(" | ".join(a["text"] for a in c["alerts"]))])
    audit(conn, user, "export", "cases.csv")
    return "﻿" + buf.getvalue()
