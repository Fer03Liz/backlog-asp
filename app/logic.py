"""Reglas de negocio del backlog: días hábiles, cruce con KGB, fechas, alertas y precios."""
import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache

TRACKING_RE = re.compile(r"\b([A-Z]{3,6}\d{5,})\b")          # ACSP005752, GEOPA21555
PART_RE = re.compile(r"\b[A-Z]{0,3}\d{3}-\d{4,5}\b")         # 661-43656, LZ661-45268
PREFIX_RE = re.compile(r"^[A-Z]{1,3}(?=\d{3}-)")

OPTION_SHORT = {
    "In Transit": "In Transit",
    "Miami": "Miami",
    "OH": "OH",
    "FG": "FG",
    "WUR": "WUR",
    "To be Defined": "Por definir",
    "Substitute Upgrade or/and Color change": "Upgrade",
}


def today() -> date:
    """Fecha de hoy. BACKLOG_HOY solo existe para pruebas."""
    forced = os.environ.get("BACKLOG_HOY")
    return date.fromisoformat(forced) if forced else date.today()


def d(value):
    return date.fromisoformat(value[:10]) if value else None


def iso(value):
    return value.isoformat() if value else None


def norm_part(part) -> str:
    return PREFIX_RE.sub("", (part or "").strip().upper())


def parse_tracking(*texts) -> list:
    found = []
    for t in texts:
        for tok in TRACKING_RE.findall(t or ""):
            if tok not in found:
                found.append(tok)
    return found


def parse_parts(text, exclude=None) -> list:
    out = []
    for p in PART_RE.findall(text or ""):
        if p != exclude and p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------- calendario
def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    dd, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - dd - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _next_monday(x: date) -> date:
    return x + timedelta(days=(7 - x.weekday()) % 7)


@lru_cache(maxsize=None)
def holidays_co(year: int) -> frozenset:
    """Festivos de Colombia (Ley 51 de 1983, 'Ley Emiliani')."""
    fixed = [date(year, 1, 1), date(year, 5, 1), date(year, 7, 20), date(year, 8, 7),
             date(year, 12, 8), date(year, 12, 25)]
    movable = [date(year, 1, 6), date(year, 3, 19), date(year, 6, 29), date(year, 8, 15),
               date(year, 10, 12), date(year, 11, 1), date(year, 11, 11)]
    e = _easter(year)
    easter_based = [e - timedelta(days=3), e - timedelta(days=2),          # jueves y viernes santo
                    e + timedelta(days=43), e + timedelta(days=64), e + timedelta(days=71)]
    return frozenset(fixed) | frozenset(_next_monday(x) for x in movable) | frozenset(easter_based)


class Calendar:
    def __init__(self, extra=()):
        self.extra = {d(x) if isinstance(x, str) else x for x in extra}

    def is_holiday(self, x: date) -> bool:
        return x in holidays_co(x.year) or x in self.extra

    def is_business(self, x: date) -> bool:
        return x.weekday() < 5 and not self.is_holiday(x)

    def next_business_after(self, x: date) -> date:
        y = x + timedelta(days=1)
        while not self.is_business(y):
            y += timedelta(days=1)
        return y

    def first_business_from(self, x: date) -> date:
        return x if self.is_business(x) else self.next_business_after(x)

    def business_days_between(self, a: date, b: date) -> int:
        """Días hábiles en el intervalo (a, b]."""
        if not a or not b or b <= a:
            return 0
        n, x = 0, a
        while x < b:
            x += timedelta(days=1)
            n += self.is_business(x)
        return n

    def add_business_days(self, x: date, n: int) -> date:
        for _ in range(n):
            x = self.next_business_after(x)
        return x


# ---------------------------------------------------------------- contexto
class Context:
    """Todo lo que se necesita para enriquecer los casos, cargado una sola vez por petición."""

    def __init__(self, conn):
        from .db import get_settings
        self.settings = get_settings(conn)
        self.today = today()
        self.cal = Calendar(r["day"] for r in conn.execute("SELECT day FROM holidays_extra"))
        self.tol = int(self.settings.get("fr_tolerance_days", "2"))
        self.tol_business = self.settings.get("fr_tolerance_business", "0") == "1"
        self.grace = int(self.settings.get("despachado_grace_days", "3"))
        self.kam_days = int(self.settings.get("kam_alert_days", "2"))
        self.oh_max = int(self.settings.get("oh_max_days", "1"))
        self.exchange_codes = {c.strip().upper() for c in self.settings.get("exchange_codes", "OO").split(",") if c.strip()}
        self.currency = self.settings.get("currency", "USD")

        self.kgb_by_trk = defaultdict(list)
        self.kgb_by_part = {}
        for r in conn.execute("SELECT * FROM kgb"):
            r = dict(r)
            if r["tracking"]:
                self.kgb_by_trk[r["tracking"]].append(r)
            p = self.kgb_by_part.setdefault(r["prod_norm"], {"qty": 0.0, "edd": None, "guides": set()})
            p["qty"] += r["qty"] or 0
            if r["edd"] and (p["edd"] is None or r["edd"] < p["edd"]):
                p["edd"] = r["edd"]
            if r["tracking"]:
                p["guides"].add(r["tracking"])
        last = conn.execute("SELECT file_date FROM loads WHERE kind='kgb' ORDER BY file_date DESC, id DESC LIMIT 1").fetchone()
        self.kgb_date = last["file_date"] if last else None

        self.prices, self.prices_norm = {}, {}
        for r in conn.execute("SELECT * FROM prices"):
            self.prices[r["part"]] = dict(r)
            self.prices_norm.setdefault(r["part_norm"], dict(r))
        self.arrivals = {r["tracking"]: r["arrived_on"] for r in conn.execute("SELECT * FROM guide_arrivals")}
        self.shipto = {r["ship_to"]: dict(r) for r in conn.execute("SELECT * FROM shipto")}


def _alert(code, level, text):
    return {"code": code, "level": level, "text": text}


def fmt(x: date) -> str:
    return x.strftime("%d/%m/%Y") if x else "—"


def enrich(c: dict, ctx: Context) -> dict:
    """Agrega fechas calculadas, cruce KGB, precio, alertas y prioridad a un caso."""
    t, cal = ctx.today, ctx.cal
    opt = c.get("option_jenny") or ""
    eta = d(c.get("eta_jenny"))
    tokens = json.loads(c.get("tracking") or "[]")
    subs_norm = {norm_part(p) for p in json.loads(c.get("subs") or "[]")}
    pnorm = norm_part(c.get("assembly"))
    closed = c.get("source_status") == "cerrado"
    shipped = d(c.get("fecha_despacho"))
    pending = not closed and not shipped

    # --- Cruce con KGB por guía (número de tracking que escribe la KAM)
    guide_rows = [r for tk in tokens for r in ctx.kgb_by_trk.get(tk, [])]
    mine = []
    if not tokens:
        match = "sin_guia"
    elif not guide_rows:
        match = "guia_no_kgb"
    else:
        mine = [r for r in guide_rows if r["prod_norm"] == pnorm]
        if mine:
            match = "guia_parte"
        else:
            mine = [r for r in guide_rows if r["prod_norm"] in subs_norm]
            match = "guia_sustituto" if mine else "guia_sin_parte"
    edds = sorted(r["edd"] for r in mine if r["edd"])
    kgb_edd = d(edds[0]) if edds else None
    kgb_event = next((r["current_event"] for r in mine if r["current_event"]), None)
    kgb_problem = next((r for r in mine if (r["current_event"] or "").lower().startswith("shipment delay")
                        or "out" in (r["sla_ind"] or "").lower() or r["issue"]), None)
    part_ref = ctx.kgb_by_part.get(pnorm)

    # --- Llegada y despacho posible (no se despacha el mismo día ni fines de semana/festivos)
    arrived_guides = [ctx.arrivals[tk] for tk in tokens if tk in ctx.arrivals]
    received = d(c.get("fecha_recepcion")) or (d(min(arrived_guides)) if arrived_guides else None)
    if received:
        llegada, fuente = received, "Recibido"
    elif opt == "OH":
        llegada, fuente = d(c.get("oh_since")) or t, "En inventario"
    elif opt == "In Transit" and kgb_edd:
        llegada, fuente = kgb_edd, "KGB (guía)"
    elif eta:
        llegada, fuente = eta, "ETA Apple"
    else:
        llegada, fuente = None, "Sin fecha"

    if shipped:
        despacho = shipped
    elif llegada:
        despacho = max(cal.next_business_after(llegada), cal.first_business_from(t))
    else:
        despacho = None
    if not eta:
        limite = None
    elif ctx.tol_business:
        limite = cal.add_business_days(eta, ctx.tol)
    else:
        limite = eta + timedelta(days=ctx.tol)
    fecha_cliente = d(c.get("fecha_objetivo")) or despacho

    # --- Alertas
    alerts = []
    if pending:
        if limite and t > limite:
            alerts.append(_alert("FUERA_FR", "rojo", f"Venció el plazo de fill rate (ETA + {ctx.tol} días) el {fmt(limite)}; despachar cuanto antes."))
        elif limite and despacho and despacho > limite:
            alerts.append(_alert("RIESGO_FR", "rojo", f"El despacho posible ({fmt(despacho)}) queda después del límite de fill rate ({fmt(limite)})."))
        elif limite and despacho and despacho == limite:
            alerts.append(_alert("SIN_MARGEN", "ambar", f"Sin margen: el despacho posible ({fmt(despacho)}) es el último día del plazo de fill rate."))
        if opt == "OH":
            since = d(c.get("oh_since")) or t
            if cal.business_days_between(since, t) > ctx.oh_max:
                alerts.append(_alert("OH_DEMORADO", "rojo", f"OH sin despachar desde el {fmt(since)}."))
        if eta and not cal.is_business(eta):
            alerts.append(_alert("ETA_NO_HABIL", "info", f"La ETA de Apple cae en día no hábil ({fmt(eta)}); no se despacha ese día."))
        if opt == "In Transit" and not received:
            if match == "guia_no_kgb":
                alerts.append(_alert("GUIA_NO_KGB", "ambar", f"La guía {', '.join(tokens)} no aparece en el KGB del {ctx.kgb_date or '—'}."))
            elif match == "guia_sin_parte":
                alerts.append(_alert("GUIA_SIN_PARTE", "ambar", "La guía está en el KGB, pero no trae esta parte ni sus sustitutos."))
            elif match == "sin_guia":
                alerts.append(_alert("SIN_GUIA", "info", "In Transit sin número de guía en los comentarios."))
            if eta and kgb_edd and eta < kgb_edd:
                alerts.append(_alert("ETA_ANTES_KGB", "ambar", f"La ETA ({fmt(eta)}) es anterior a la llegada que muestra el KGB ({fmt(kgb_edd)})."))
        notes_l = (c.get("notes_jenny") or "").lower()
        if opt == "In Transit" and kgb_problem:
            alerts.append(_alert("GUIA_RETRASADA", "ambar", f"La guía {kgb_problem['tracking']} aparece fuera de SLA o con retraso en el KGB."))
        elif opt == "In Transit" and "delayed" in notes_l and (not tokens or any(tk.lower() in notes_l for tk in tokens)):
            alerts.append(_alert("GUIA_RETRASADA", "ambar", "Apple reporta la guía como retrasada (Notes)."))
        if llegada and fuente in ("KGB (guía)", "ETA Apple") and opt in ("In Transit", "Miami") and llegada < t:
            alerts.append(_alert("SIN_RECEPCION", "ambar", f"Debió llegar el {fmt(llegada)} y no hay recepción registrada."))
        if opt == "Miami" and part_ref and part_ref["qty"] > 0:
            alerts.append(_alert("POSIBLE_DOBLE_COMPRA", "ambar",
                                 f"Esta parte viene en el KGB: {int(part_ref['qty'])} unid., primera llegada {fmt(d(part_ref['edd']))}."))
        if c.get("aprobacion_kam") == "Pendiente":
            sol = d(c.get("kam_solicitado"))
            if sol and (t - sol).days > ctx.kam_days:
                alerts.append(_alert("KAM_PENDIENTE", "ambar", f"Aprobación de la KAM pendiente desde el {fmt(sol)}."))
    if shipped and not closed and eta and cal.business_days_between(eta, t) > ctx.grace:
        alerts.append(_alert("DESPACHADO_SIGUE", "info", "Despachado y sigue en el reporte; confirmar cierre con la tienda."))
    if c.get("reopened") and not closed:
        alerts.append(_alert("REABIERTO", "ambar", "El caso había salido del reporte y volvió a aparecer."))

    levels = {a["level"] for a in alerts}
    nivel = "rojo" if "rojo" in levels else "ambar" if "ambar" in levels else "info" if "info" in levels else "ok"

    # --- Precio según garantía
    pr = ctx.prices.get(c.get("assembly")) or ctx.prices_norm.get(pnorm)
    tipo_precio = "exchange" if (c.get("warranty") or "").upper() in ctx.exchange_codes else "stocking"
    precio = pr.get(tipo_precio) if pr else None

    # --- Prioridad (menor = más urgente): OH, luego rojos, luego cercanía al límite
    super_asp = (c.get("repair_strategy") or "").upper() == "Z8.2"
    if closed:
        tier = 9
    elif shipped:
        tier = 6
    elif opt == "OH":
        tier = 0
    elif nivel == "rojo":
        tier = 1
    elif nivel == "ambar":
        tier = 2
    else:
        tier = 3
    dias_limite = (limite - t).days if limite else None
    prioridad = tier * 100000 + (max(min(dias_limite if dias_limite is not None else 999, 9999), -999) + 1000) * 10 + (0 if super_asp else 1)

    return {
        **c,
        "option_short": OPTION_SHORT.get(opt, opt or "—"),
        "tracking_list": tokens,
        "subs_list": json.loads(c.get("subs") or "[]"),
        "super_asp": super_asp,
        "pendiente": pending,
        "dias_eta": (eta - t).days if eta else None,
        "limite_fr": iso(limite),
        "dias_limite": dias_limite,
        "llegada": iso(llegada),
        "llegada_fuente": fuente,
        "despacho_posible": iso(despacho),
        "fecha_cliente": iso(fecha_cliente),
        "edad": (t - d(c["created_on"])).days if c.get("created_on") else None,
        "kgb_match": match,
        "kgb_edd": iso(kgb_edd),
        "kgb_evento": kgb_event,
        "parte_kgb_qty": part_ref["qty"] if part_ref else 0,
        "parte_kgb_edd": part_ref["edd"] if part_ref else None,
        "precio": precio,
        "tipo_precio": tipo_precio,
        "moneda": ctx.currency,
        "alerts": alerts,
        "nivel": nivel,
        "prioridad": prioridad,
        "tienda_email": (ctx.shipto.get(c.get("ship_to")) or {}).get("email"),
    }


def load_cases(conn, ctx: Context, include_closed: bool = False, closed_days: int = 45) -> list:
    if include_closed:
        since = iso(ctx.today - timedelta(days=closed_days))
        rows = conn.execute("SELECT * FROM cases WHERE source_status='abierto' OR closed_on >= ?", (since,))
    else:
        rows = conn.execute("SELECT * FROM cases WHERE source_status='abierto'")
    out = [enrich(dict(r), ctx) for r in rows]
    out.sort(key=lambda x: x["prioridad"])
    return out


# ---------------------------------------------------------------- indicadores
def kpis(conn, ctx: Context) -> dict:
    t = ctx.today
    all_rows = [enrich(dict(r), ctx) for r in conn.execute("SELECT * FROM cases")]
    open_ = [c for c in all_rows if c["source_status"] == "abierto"]
    pend = [c for c in open_ if c["pendiente"]]

    def val(rows):
        vals = [c["precio"] for c in rows if c["precio"] is not None]
        return {"total": round(sum(vals), 2), "con_precio": len(vals), "sin_precio": len(rows) - len(vals)}

    # Fill rate: líneas cuyo límite (ETA + tolerancia) ya pasó en los últimos 30 días
    first = conn.execute("SELECT MIN(file_date) m FROM loads WHERE kind='backlog'").fetchone()["m"]
    start = max(t - timedelta(days=30), d(first)) if first else t - timedelta(days=30)
    due = [c for c in all_rows if c["limite_fr"] and start <= d(c["limite_fr"]) < t]
    with_date = [c for c in due if c["fecha_despacho"]]
    on_time = [c for c in with_date if d(c["fecha_despacho"]) <= d(c["limite_fr"])]
    no_record = [c for c in due if not c["fecha_despacho"] and c["source_status"] == "cerrado"]
    late_open = [c for c in due if not c["fecha_despacho"] and c["source_status"] == "abierto"]
    fr_den = len(with_date) + len(late_open)
    any_shipped = any(c["fecha_despacho"] for c in all_rows)
    fill_rate = {
        "valor": round(100 * len(on_time) / fr_den, 1) if fr_den and any_shipped else None,
        "a_tiempo": len(on_time), "evaluadas": fr_den, "sin_registro": len(no_record), "desde": iso(start),
    }

    # Proyección: lo que vence en los próximos 7 días
    horizon = t + timedelta(days=7)
    upcoming = [c for c in pend if c["limite_fr"] and t <= d(c["limite_fr"]) <= horizon]
    at_risk = [c for c in upcoming if c["nivel"] == "rojo"]

    # Flujo diario desde las cargas del backlog
    flujo = []
    for r in conn.execute("SELECT file_date, summary FROM loads WHERE kind='backlog' ORDER BY file_date DESC, id DESC LIMIT 15"):
        s = json.loads(r["summary"] or "{}")
        flujo.append({"fecha": r["file_date"], "nuevas": s.get("nuevas", 0), "salieron": s.get("cerradas", 0),
                      "neto": s.get("nuevas", 0) - s.get("cerradas", 0), "abiertas": s.get("abiertas", 0)})
    flujo.reverse()

    buckets = {"0-7": 0, "8-15": 0, "16-30": 0, "31+": 0}
    for c in pend:
        e = c["edad"] or 0
        buckets["0-7" if e <= 7 else "8-15" if e <= 15 else "16-30" if e <= 30 else "31+"] += 1

    changed = [c for c in open_ if c["eta_changes"]]
    slips = [(d(c["eta_jenny"]) - d(c["eta_first"])).days for c in changed if c["eta_jenny"] and c["eta_first"]]

    by_opt = defaultdict(lambda: {"abiertas": 0, "despachadas": 0, "ciclo": []})
    for c in all_rows:
        k = c["option_short"]
        if c["pendiente"]:
            by_opt[k]["abiertas"] += 1
        if c["fecha_despacho"] and c["created_on"]:
            by_opt[k]["despachadas"] += 1
            by_opt[k]["ciclo"].append((d(c["fecha_despacho"]) - d(c["created_on"])).days)
    por_opcion = [{"opcion": k, "abiertas": v["abiertas"], "despachadas": v["despachadas"],
                   "ciclo_prom": round(sum(v["ciclo"]) / len(v["ciclo"]), 1) if v["ciclo"] else None}
                  for k, v in sorted(by_opt.items(), key=lambda kv: -kv[1]["abiertas"])]

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
        "proyeccion": {"vencen_7d": len(upcoming), "en_riesgo": len(at_risk)},
        "flujo": flujo,
        "edad": buckets,
        "eta": {"con_cambios": len(changed), "abiertas": len(open_),
                "corrimiento_prom": round(sum(slips) / len(slips), 1) if slips else None},
        "por_opcion": por_opcion,
        "oh": {"total": len(oh), "demorados": sum(1 for c in oh if c["nivel"] == "rojo")},
        "kam": {"pendientes": len(kam),
                "dias_prom": round(sum((t - d(c["kam_solicitado"])).days for c in kam if c["kam_solicitado"]) / len(kam), 1) if kam else None},
        "top_partes": top("assembly", "parte"),
        "top_tiendas": top("ship_to", "ship_to"),
        "alertas": {lvl: sum(1 for c in pend if c["nivel"] == lvl) for lvl in ("rojo", "ambar", "info", "ok")},
    }


# ---------------------------------------------------------------- correos
def email_groups(conn, ctx: Context, fecha: date, modo: str, signature: str) -> list:
    """Correos por tienda con solo tiempos: notificación, PO y fecha."""
    cases = [c for c in load_cases(conn, ctx) if c["pendiente"] or
             (c["fecha_despacho"] and d(c["fecha_despacho"]) >= fecha - timedelta(days=1))]
    if modo == "gestionados":
        day = iso(fecha)
        touched = {r["target"] for r in conn.execute(
            "SELECT DISTINCT target FROM audit_log WHERE action IN ('case_update','comment') AND substr(at,1,10)=?", (day,))}
        cases = [c for c in cases if c["key_notif"] in touched]
    groups = defaultdict(list)
    for c in cases:
        groups[c["ship_to"]].append(c)
    out = []
    for ship_to, rows in sorted(groups.items(), key=lambda kv: (kv[1][0]["list_name"] or "")):
        rows.sort(key=lambda c: (c["notifctn"] or "", c["line_item"] or 0))
        store = rows[0]["list_name"] or ship_to
        lines = []
        for c in rows:
            ref = f"Notificación {c['notifctn']}-{int(c['line_item'] or 0):02d}"
            po = f"PO {c['po_number']}" if c.get("po_number") else "PO —"
            if c["fecha_despacho"]:
                when = f"Despachado el {fmt(d(c['fecha_despacho']))}"
            else:
                when = f"Fecha estimada de despacho: {fmt(d(c['fecha_cliente']))}"
            lines.append(f"{ref} | {po} | {when}")
        subject = f"Estado de sus casos pendientes – {store.strip()} – {fmt(fecha)}"
        body = ("Buenos días,\n\nLes compartimos el estado de los casos pendientes de repuesto:\n\n"
                + "\n".join(lines)
                + "\n\nLas fechas corresponden al despacho desde nuestro centro de distribución. "
                  "Si alguna cambia, se la informaremos.\n\nSaludos,\n" + signature)
        info = ctx.shipto.get(ship_to) or {}
        out.append({"ship_to": ship_to, "tienda": store.strip(), "email": info.get("email"),
                    "casos": [c["key_notif"] for c in rows], "fechas": {c["key_notif"]: c["fecha_cliente"] for c in rows},
                    "subject": subject, "body": body})
    return out
