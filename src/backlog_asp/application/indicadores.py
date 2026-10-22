"""Indicadores del tablero: fill rate, aging, flujo diario, tiendas y valor del backlog."""
import json
from collections import defaultdict
from datetime import timedelta

from ..domain.calendario import d, iso
from ..domain.casos import enrich


def kpis(conn, ctx) -> dict:
    t = ctx.today
    casos, primera, snaps, cargas = conn.select_many([
        ("SELECT * FROM cases", ()),
        ("SELECT MIN(file_date) m FROM loads WHERE kind='backlog'", ()),
        ("SELECT s.file_date, c.ship_to FROM snapshots s JOIN cases c ON c.key_notif = s.key_notif", ()),
        ("SELECT TOP 15 file_date, summary FROM loads WHERE kind='backlog' ORDER BY file_date DESC, id DESC", ()),
    ])
    all_rows = [enrich(dict(r), ctx) for r in casos]
    open_ = [c for c in all_rows if c["source_status"] == "abierto"]
    pend = [c for c in open_ if c["pendiente"]]

    def val(rows):
        vals = [c["precio"] for c in rows if c["precio"] is not None]
        return {"total": round(sum(vals), 2), "con_precio": len(vals), "sin_precio": len(rows) - len(vals)}

    # Fill rate: líneas cuya primera ETA de Apple ya pasó (últimos 30 días); a tiempo si se despacharon
    # en esa ETA o antes. La primera ETA no se mueve aunque Apple la corra después.
    first = primera[0]["m"] if primera else None
    start = max(t - timedelta(days=30), d(first)) if first else t - timedelta(days=30)
    meta = lambda c: d(c.get("eta_first") or c.get("eta_jenny"))
    due = [c for c in all_rows if meta(c) and start <= meta(c) < t]
    with_date = [c for c in due if c["fecha_despacho"]]
    on_time = [c for c in with_date if d(c["fecha_despacho"]) <= meta(c)]
    no_record = [c for c in due if not c["fecha_despacho"] and c["source_status"] == "cerrado"]
    late_open = [c for c in due if not c["fecha_despacho"] and c["source_status"] == "abierto"]
    fr_den = len(with_date) + len(late_open)
    any_shipped = any(c["fecha_despacho"] for c in all_rows)
    fill_rate = {
        "valor": round(100 * len(on_time) / fr_den, 1) if fr_den and any_shipped else None,
        "a_tiempo": len(on_time), "evaluadas": fr_den, "sin_registro": len(no_record), "desde": iso(start),
    }

    # Aging: edad promedio del backlog pendiente y días promedio de la creación al despacho (últimos 30 días)
    edades = [c["edad"] for c in pend if c["edad"] is not None]
    ciclos = [(d(c["fecha_despacho"]) - d(c["created_on"])).days for c in all_rows
              if c["fecha_despacho"] and c["created_on"] and d(c["fecha_despacho"]) >= t - timedelta(days=30)]
    aging = {"backlog_prom": round(sum(edades) / len(edades), 1) if edades else None, "backlog_lineas": len(edades),
             "despacho_prom": round(sum(ciclos) / len(ciclos), 1) if ciclos else None, "despachadas_30d": len(ciclos)}

    # Proyección: lo que vence en los próximos 7 días
    horizon = t + timedelta(days=7)
    upcoming = [c for c in pend if c["limite_fr"] and t <= d(c["limite_fr"]) <= horizon]
    at_risk = [c for c in upcoming if c["nivel"] == "rojo"]

    # Flujo diario desde las cargas del backlog
    tiendas_por_carga = {}
    for r in snaps:
        tiendas_por_carga.setdefault(r["file_date"], set()).add(r["ship_to"])
    flujo = []
    for r in cargas:
        s = json.loads(r["summary"] or "{}")
        flujo.append({"fecha": r["file_date"], "nuevas": s.get("nuevas", 0), "salieron": s.get("cerradas", 0),
                      "neto": s.get("nuevas", 0) - s.get("cerradas", 0), "abiertas": s.get("abiertas", 0),
                      "tiendas": len(tiendas_por_carga.get(r["file_date"], ()))})
    flujo.reverse()

    buckets = {"0-7": 0, "8-15": 0, "16-30": 0, "31+": 0}
    for c in pend:
        e = c["edad"] or 0
        buckets["0-7" if e <= 7 else "8-15" if e <= 15 else "16-30" if e <= 30 else "31+"] += 1

    proy = [c["edad_despacho"] for c in pend if c["edad_despacho"] is not None]
    real = [c["edad_despacho"] for c in all_rows if c["fecha_despacho"] and c["edad_despacho"] is not None]
    rangos = {"0-7": 0, "8-15": 0, "16-30": 0, "31+": 0}
    for e in proy:
        rangos["0-7" if e <= 7 else "8-15" if e <= 15 else "16-30" if e <= 30 else "31+"] += 1
    edad_despacho = {"proyectada_prom": round(sum(proy) / len(proy), 1) if proy else None,
                     "real_prom": round(sum(real) / len(real), 1) if real else None,
                     "despachadas": len(real), "rangos": rangos}

    changed = [c for c in open_ if c["eta_changes"]]
    slips = [(d(c["eta_jenny"]) - d(c["eta_first"])).days for c in changed if c["eta_jenny"] and c["eta_first"]]

    by_opt = defaultdict(lambda: {"abiertas": 0, "despachadas": 0, "ciclo": [], "proyectado": []})
    for c in all_rows:
        k = c["option_short"]
        if c["pendiente"]:
            by_opt[k]["abiertas"] += 1
            if c["edad_despacho"] is not None:
                by_opt[k]["proyectado"].append(c["edad_despacho"])
        if c["fecha_despacho"] and c["created_on"]:
            by_opt[k]["despachadas"] += 1
            by_opt[k]["ciclo"].append((d(c["fecha_despacho"]) - d(c["created_on"])).days)
    por_opcion = [{"opcion": k, "abiertas": v["abiertas"], "despachadas": v["despachadas"],
                   "ciclo_prom": round(sum(v["ciclo"]) / len(v["ciclo"]), 1) if v["ciclo"] else None,
                   "proyectado_prom": round(sum(v["proyectado"]) / len(v["proyectado"]), 1) if v["proyectado"] else None}
                  for k, v in sorted(by_opt.items(), key=lambda kv: -kv[1]["abiertas"])]

    # Tiendas (AASP = Ship-to) con líneas por despachar
    por_tienda = defaultdict(lambda: {"lineas": 0, "rojas": 0})
    for c in pend:
        pt = por_tienda[c["ship_to"]]
        pt["lineas"] += 1
        pt["rojas"] += c["nivel"] == "rojo"
    top10 = sum(sorted((v["lineas"] for v in por_tienda.values()), reverse=True)[:10])
    clientes = {(ctx.shipto.get(c["ship_to"]) or {}).get("cliente") or ((c["list_name"] or "").split() or ["—"])[0]
                for c in pend}
    fechas = sorted(tiendas_por_carga, reverse=True)[:2]
    ultima = tiendas_por_carga[fechas[0]] if fechas else set()
    previa = tiendas_por_carga[fechas[1]] if len(fechas) > 1 else None
    tiendas = {
        "con_backlog": len(por_tienda),
        "clientes": len(clientes),
        "con_rojas": sum(1 for v in por_tienda.values() if v["rojas"]),
        "top10_pct": round(100 * top10 / len(pend), 1) if pend else None,
        "entraron": len(ultima - previa) if previa is not None else None,
        "salieron": len(previa - ultima) if previa is not None else None,
    }

    oh = [c for c in pend if c["option_jenny"] == "OH"]
    kam = [c for c in pend if c["aprobacion_kam"] == "Pendiente"]

    def top(key, label):
        agg = defaultdict(lambda: {"lineas": 0, "valor": None, "rojas": 0, "desc": ""})
        for c in pend:
            a = agg[c[key]]
            a["lineas"] += 1
            if c["precio"] is not None:
                a["valor"] = (a["valor"] or 0) + c["precio"]
            a["rojas"] += c["nivel"] == "rojo"
            a["desc"] = c["description"] if key == "assembly" else c["list_name"]
        return [{label: k, **v, "valor": round(v["valor"], 2) if v["valor"] is not None else None} for k, v in
                sorted(agg.items(), key=lambda kv: (-kv[1]["lineas"], -(kv[1]["valor"] or 0)))[:10]]

    return {
        "hoy": iso(t),
        "moneda": ctx.currency,
        "abiertas_reporte": len(open_),
        "pendientes_despacho": len(pend),
        "reparaciones": len({c["notifctn"] for c in pend}),
        "valor": val(pend),
        "fill_rate": fill_rate,
        "aging": aging,
        "proyeccion": {"vencen_7d": len(upcoming), "en_riesgo": len(at_risk)},
        "flujo": flujo,
        "edad": buckets,
        "edad_despacho": edad_despacho,
        "eta": {"con_cambios": len(changed), "abiertas": len(open_),
                "corrimiento_prom": round(sum(slips) / len(slips), 1) if slips else None},
        "por_opcion": por_opcion,
        "oh": {"total": len(oh), "demorados": sum(1 for c in oh if c["nivel"] == "rojo")},
        "kam": {"pendientes": len(kam),
                "dias_prom": round(sum((t - d(c["kam_solicitado"])).days for c in kam if c["kam_solicitado"]) / len(kam), 1) if kam else None},
        "top_partes": top("assembly", "parte"),
        "top_tiendas": top("ship_to", "ship_to"),
        "tiendas": tiendas,
        "alertas": {lvl: sum(1 for c in pend if c["nivel"] == lvl) for lvl in ("rojo", "ambar", "info", "ok")},
    }
