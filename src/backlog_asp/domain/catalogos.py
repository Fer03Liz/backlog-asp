"""Catálogos y parámetros por defecto del negocio."""

DEFAULT_SETTINGS = {
    "kgb_cust_ids": "460241",          # cliente del KGB (cambiará a Ingram el 2-nov)
    "fr_tolerance_days": "2",          # atendido = despacho hasta ETA + N días
    "fr_tolerance_business": "0",      # 0 = días calendario, 1 = días hábiles (confirmar con la KAM)
    "exchange_codes": "OO",            # garantías que se pagan a precio exchange
    "despachado_grace_days": "3",      # días hábiles tras la ETA antes de avisar que sigue en el reporte
    "kam_alert_days": "2",             # días esperando a la KAM antes de alertar
    "oh_max_days": "1",                # días hábiles máximos de un OH sin despachar
    "email_transit_days": "0",         # 0 = en el correo solo llevan fecha los repuestos en bodega
    "oo_transit_days": "22",           # Open Orders: días de Apple a Colombia después del Recovery ETA
    "miami_lead_days": "7",            # Miami: días desde la compra hasta la llegada (por defecto)
    "currency": "COP",
    "email_signature": "Equipo de Planeación ASP\nIngram Micro",
}
SETTINGS_ENTEROS = {"fr_tolerance_days", "despachado_grace_days", "kam_alert_days", "oh_max_days",
                    "email_transit_days", "oo_transit_days", "miami_lead_days"}

ESTADOS = ["Nuevo", "En gestión", "Cotización enviada", "Pendiente aprobación ACM",
           "Recibido", "Despachado", "Sin solución"]
APROBACION_KAM = ["No requiere", "Pendiente", "Aprobada", "Rechazada"]
PROPUESTAS = ["", "Mantener", "FG", "Upgrade", "Miami", "Sustituto", "Otra"]
ROLES = ["admin", "editor", "lector", "acm", "tienda"]
