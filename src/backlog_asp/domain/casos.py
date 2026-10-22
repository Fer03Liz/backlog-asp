"""Reglas de un caso del backlog: cruce con el KGB, fechas, alertas, precio y prioridad.

`ctx` es la foto de datos que arma la capa de aplicación (application/contexto.py): KGB, precios, partes,
llegadas de guías, compras en Miami, inventario asignado, notas sin leer y parámetros.
"""
import json
from datetime import timedelta

from .calendario import d, fmt, iso
from .partes import OPTION_SHORT, norm_part


def _alert(code, level, text):
    return {"code": code, "level": level, "text": text}


def nivel_de(alerts) -> str:
    levels = {a["level"] for a in alerts}
    return "rojo" if "rojo" in levels else "ambar" if "ambar" in levels else "info" if "info" in levels else "ok"


# ---------------------------------------------------------------- alertas de Miami, inventario y notas de la ACM
def alertas_miami(c, ctx, compra, limite, pnorm, sustituto) -> list:
    """Alertas de Miami para un caso pendiente."""
    t, cal, out = ctx.today, ctx.cal, []
    opt = c.get("option_jenny") or ""
    if compra:
        eta = d(compra.get("eta"))
        grupo = {pnorm} | ({norm_part(sustituto)} if sustituto else set())
        kgb_edd = min((ctx.kgb_by_part[p]["edd"] for p in grupo if p in ctx.kgb_by_part and ctx.kgb_by_part[p]["edd"]), default=None)
        oo_lleg = min((ctx.oo_llegada[p] for p in grupo if p in ctx.oo_llegada), default=None)
        for fuente, f in (("el KGB (con guía)", d(kgb_edd)), ("un pedido en Open Orders", d(oo_lleg))):
            if not f:
                continue
            despacho = max(cal.next_business_after(f), cal.first_business_from(t))
            if limite and despacho <= limite:
                motivo = f"alcanza a despacharse dentro del límite ({fmt(limite)})"
            elif eta and f < eta:
                motivo = f"llega antes que Miami ({fmt(eta)})"
            else:
                continue
            out.append(_alert("MIAMI_EVALUAR", "ambar",
                              f"Apple trae esta parte por {fuente} hacia el {fmt(f)} y {motivo}. "
                              "Evaluar cancelar la compra en Miami si esa unidad no está asignada a otro caso."))
            break
        if eta and eta < t:
            out.append(_alert("MIAMI_ATRASADA", "ambar",
                              f"La compra en Miami debía llegar el {fmt(eta)} y no está marcada como recibida."))
    elif opt == "Miami":
        out.append(_alert("MIAMI_SIN_COMPRA", "info", "Solución Miami sin compra registrada en el tablero."))
    return out


def alertas_inventario(c, ctx, inv) -> list:
    """Alertas de inventario propio para un caso pendiente."""
    if not ctx.inv_fecha:
        return []
    fecha = fmt(d(ctx.inv_fecha))
    opt = c.get("option_jenny") or ""
    if inv and opt != "OH":
        extra = " (con el sustituto oficial)" if inv["via"] == "sustituto" else ""
        return [_alert("INV_DISPONIBLE", "ambar",
                       f"Hay inventario propio de {inv['parte']}{extra} en el archivo del {fecha}: este caso se puede despachar.")]
    if opt == "OH" and not inv and not c.get("fecha_recepcion"):
        return [_alert("INV_OH_SIN_STOCK", "ambar",
                       f"La ACM lo marca OH, pero el inventario del {fecha} no tiene unidades libres de esta parte.")]
    return []


def alertas_notas(c, ctx) -> list:
    """Notas de la ACM que nadie del equipo ha marcado leídas."""
    n = ctx.notas_acm.get(c["key_notif"])
    if not n:
        return []
    extra = f" (y {n['n'] - 1} más)" if n["n"] > 1 else ""
    texto = n["text"] if len(n["text"]) <= 120 else n["text"][:117] + "…"
    return [_alert("NOTA_ACM", "ambar", f"Nota nueva de la ACM ({n['username']}, {n['at'][:10]}){extra}: «{texto}»")]


# ---------------------------------------------------------------- enriquecimiento
def enrich(c: dict, ctx) -> dict:
    """Agrega fechas calculadas, cruce KGB, precio, alertas y prioridad a un caso."""
    t, cal = ctx.today, ctx.cal
    opt = c.get("option_jenny") or ""
    eta = d(c.get("eta_jenny"))
    tokens = json.loads(c.get("tracking") or "[]")
    subs_norm = {norm_part(p) for p in json.loads(c.get("subs") or "[]")}
    pnorm = norm_part(c.get("assembly"))
    master = ctx.parts.get((c.get("assembly") or "").upper()) or ctx.parts_norm.get(pnorm) or {}
    sustituto = master.get("substitute")          # sustituto oficial según el Parts Data
    if sustituto and norm_part(sustituto) != pnorm:
        subs_norm.add(norm_part(sustituto))
    closed = c.get("source_status") == "cerrado"
    shipped = d(c.get("fecha_despacho"))
    pending = not closed and not shipped
    compra = ctx.miami.get(c.get("key_notif"))     # compra en Miami activa
    inv = ctx.inv_asignado.get(c.get("key_notif"))  # unidad de inventario propio asignada

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
    # Retrasada = Apple reporta demora (evento Shipment Delay o issue). "Out of SLA" es el SLA interno de Apple:
    # solo informa, salvo que la EDD quede después de la ETA (eso lo cubre ETA_ANTES_KGB / GUIA_RETRASADA abajo).
    kgb_problem = next((r for r in mine if (r["current_event"] or "").lower().startswith("shipment delay") or r["issue"]), None)
    kgb_fuera_sla = next((r for r in mine if "out" in (r["sla_ind"] or "").lower()), None)
    refs = [ctx.kgb_by_part[x] for x in {pnorm, norm_part(sustituto)} if x and x in ctx.kgb_by_part]
    part_ref = {"qty": sum(r["qty"] for r in refs), "edd": min((r["edd"] for r in refs if r["edd"]), default=None),
                "guides": set().union(*(r["guides"] for r in refs))} if refs else None

    # --- Llegada y despacho posible (no se despacha el mismo día ni fines de semana/festivos)
    arrived_guides = [ctx.arrivals[tk] for tk in tokens if tk in ctx.arrivals]
    received = d(c.get("fecha_recepcion")) or (d(min(arrived_guides)) if arrived_guides else None)
    if received:
        llegada, fuente = received, "Recibido"
    elif opt == "OH":
        llegada, fuente = d(c.get("oh_since")) or t, "En inventario"
    elif inv:
        llegada, fuente = d(ctx.inv_fecha), "Inventario propio"
    elif opt == "In Transit" and kgb_edd:
        llegada, fuente = kgb_edd, "KGB (guía)"
    elif compra and compra.get("eta"):
        llegada, fuente = d(compra["eta"]), "Compra Miami"
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
            alerts.append(_alert("FUERA_FR", "rojo", f"Venció el plazo (ETA + {ctx.tol} días) el {fmt(limite)}; despachar cuanto antes."))
        elif limite and despacho and despacho > limite:
            alerts.append(_alert("RIESGO_FR", "rojo", f"El despacho posible ({fmt(despacho)}) queda después del límite (ETA + {ctx.tol} días: {fmt(limite)})."))
        elif limite and despacho and despacho == limite:
            alerts.append(_alert("SIN_MARGEN", "ambar", f"Sin margen: el despacho posible ({fmt(despacho)}) es el último día del plazo (ETA + {ctx.tol} días)."))
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
            alerts.append(_alert("GUIA_RETRASADA", "ambar", f"La guía {kgb_problem['tracking']} aparece con retraso en el KGB ({kgb_problem['current_event'] or 'issue'})."))
        elif opt == "In Transit" and kgb_fuera_sla and eta and kgb_edd and kgb_edd > eta:
            alerts.append(_alert("GUIA_RETRASADA", "ambar", f"La guía {kgb_fuera_sla['tracking']} va fuera del SLA de Apple y llega después de la ETA."))
        elif opt == "In Transit" and kgb_fuera_sla:
            alerts.append(_alert("FUERA_SLA_APPLE", "info", f"Apple marca la guía {kgb_fuera_sla['tracking']} fuera de su SLA, pero la EDD ({fmt(kgb_edd)}) no pasa la ETA."))
        elif opt == "In Transit" and "delayed" in notes_l and (not tokens or any(tk.lower() in notes_l for tk in tokens)):
            alerts.append(_alert("GUIA_RETRASADA", "ambar", "Apple reporta la guía como retrasada (Notes)."))
        if llegada and fuente in ("KGB (guía)", "ETA Apple") and opt in ("In Transit", "Miami") and llegada < t:
            alerts.append(_alert("SIN_RECEPCION", "ambar", f"Debió llegar el {fmt(llegada)} y no hay recepción registrada."))
        if opt == "Miami" and not compra and part_ref and part_ref["qty"] > 0:
            alerts.append(_alert("POSIBLE_DOBLE_COMPRA", "ambar",
                                 f"Esta parte (o su sustituto) viene en el KGB: {int(part_ref['qty'])} unid., primera llegada {fmt(d(part_ref['edd']))}."))
        alerts.extend(alertas_miami(c, ctx, compra, limite, pnorm, sustituto))
        alerts.extend(alertas_inventario(c, ctx, inv))
        if c.get("aprobacion_kam") == "Pendiente":
            sol = d(c.get("kam_solicitado"))
            if sol and (t - sol).days > ctx.kam_days:
                alerts.append(_alert("KAM_PENDIENTE", "ambar", f"Aprobación del Apple Care Manager pendiente desde el {fmt(sol)}."))
    if shipped and not closed and eta and cal.business_days_between(eta, t) > ctx.grace:
        alerts.append(_alert("DESPACHADO_SIGUE", "info", "Despachado y sigue en el reporte; confirmar cierre con la tienda."))
    if c.get("reopened") and not closed:
        alerts.append(_alert("REABIERTO", "ambar", "El caso había salido del reporte y volvió a aparecer."))
    alerts.extend(alertas_notas(c, ctx))
    nivel = nivel_de(alerts)

    # --- Precio: el caso siempre vale stocking. Fuera de garantía la tienda paga exchange y Apple la diferencia.
    pr = ctx.prices.get((c.get("assembly") or "").upper()) or ctx.prices_norm.get(pnorm)
    fuera_garantia = (c.get("warranty") or "").upper() in ctx.exchange_codes
    tipo_precio = "fuera de garantía" if fuera_garantia else "en garantía"
    precio = pr.get("stocking") if pr else None
    precio_tienda = pr.get("exchange") if pr and fuera_garantia else (0 if pr else None)
    precio_apple = precio - precio_tienda if precio is not None and precio_tienda is not None else None
    precios_especiales = {k: pr.get(k) for k in ("battery", "display", "display_backglass", "backglass", "addon")
                          if pr and pr.get(k)}

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
        "edad_despacho": (despacho - d(c["created_on"])).days if despacho and c.get("created_on") else None,
        "sustituto_pd": sustituto,
        "descripcion_pd": master.get("description"),
        "kgb_match": match,
        "kgb_edd": iso(kgb_edd),
        "kgb_evento": kgb_event,
        "parte_kgb_qty": part_ref["qty"] if part_ref else 0,
        "parte_kgb_edd": part_ref["edd"] if part_ref else None,
        "precio": precio,
        "tipo_precio": tipo_precio,
        "precio_tienda": precio_tienda,
        "precio_apple": precio_apple,
        "compra_miami": compra,
        "inventario": inv,
        "precios_especiales": precios_especiales,
        "moneda": ctx.currency,
        "alerts": alerts,
        "nivel": nivel,
        "prioridad": prioridad,
        "tienda_email": (ctx.shipto.get(c.get("ship_to")) or {}).get("email"),
    }


# ---------------------------------------------------------------- vista de la tienda
EN_GESTION = {"En gestión", "Cotización enviada", "Pendiente aprobación ACM", "Sin solución"}


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
