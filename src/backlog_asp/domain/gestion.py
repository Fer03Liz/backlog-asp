"""Reglas de la gestión del equipo sobre un caso: validación, fechas protegidas y cambios automáticos entre campos."""
from datetime import date

from .calendario import today
from .catalogos import APROBACION_KAM, ESTADOS, PROPUESTAS
from .errores import GestionError

PROTEGIDAS = ("fecha_recepcion", "fecha_despacho")
NOMBRE = {"fecha_recepcion": "fecha de recepción", "fecha_despacho": "fecha de despacho"}
CAMPOS_LOTE = ("estado", "responsable", "fecha_recepcion", "fecha_despacho", "fecha_objetivo", "propuesta", "aprobacion_kam")


def fecha(valor, campo):
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor)[:10]).isoformat()
    except ValueError:
        raise GestionError(f"Fecha no válida en {campo}.", 422)


def normalizar(changes: dict) -> dict:
    out = dict(changes)
    if "estado" in out and out["estado"] not in ESTADOS:
        raise GestionError("Estado no válido.", 422)
    if "aprobacion_kam" in out and out["aprobacion_kam"] not in APROBACION_KAM:
        raise GestionError("Valor de aprobación no válido.", 422)
    if "propuesta" in out and (out["propuesta"] or "") not in PROPUESTAS:
        raise GestionError("Propuesta no válida.", 422)
    for f in ("fecha_recepcion", "fecha_despacho", "fecha_objetivo"):
        if f in out:
            out[f] = fecha(out[f], f)
    if "responsable" in out:
        out["responsable"] = (out["responsable"] or "").strip()[:80] or None
    if "propuesta" in out:
        out["propuesta"] = out["propuesta"] or None
    return out


def protegidas_bloqueadas(user, old: dict, changes: dict) -> list:
    """Fechas que este usuario no puede cambiar porque ya estaban registradas."""
    if user["role"] == "admin":
        return []
    return [f for f in PROTEGIDAS if f in changes and old[f] and changes[f] != old[f]]


def calcular(old: dict, changes: dict) -> dict:
    """Reglas automáticas entre campos. Devuelve el diff {campo: {antes, ahora}}."""
    changes = dict(changes)
    hoy = today().isoformat()
    if changes.get("estado") == "Despachado" and not (changes.get("fecha_despacho") or old["fecha_despacho"]):
        changes["fecha_despacho"] = hoy
    if changes.get("estado") == "Recibido" and not (changes.get("fecha_recepcion") or old["fecha_recepcion"]):
        changes["fecha_recepcion"] = hoy
    sin_cambio_estado = changes.get("estado", old["estado"]) == old["estado"]   # el formulario siempre envía el estado
    if changes.get("fecha_despacho") and not old["fecha_despacho"] and sin_cambio_estado:
        changes["estado"] = "Despachado"
    elif changes.get("fecha_recepcion") and not old["fecha_recepcion"] and sin_cambio_estado and old["estado"] in ("Nuevo", "En gestión"):
        changes["estado"] = "Recibido"
    if changes.get("estado") == "Pendiente aprobación ACM" and "aprobacion_kam" not in changes:
        changes["aprobacion_kam"] = "Pendiente"
    if "aprobacion_kam" in changes and changes["aprobacion_kam"] != old["aprobacion_kam"]:
        if changes["aprobacion_kam"] == "Pendiente":
            changes["kam_solicitado"], changes["kam_respuesta"] = hoy, None
        elif changes["aprobacion_kam"] in ("Aprobada", "Rechazada"):
            changes["kam_respuesta"] = hoy
    return {k: {"antes": old[k], "ahora": v} for k, v in changes.items() if old[k] != v}


def recepcion(case: dict, fecha_llegada: str) -> dict:
    """Diff que deja un caso recibido (llegada de guía o compra en Miami), sin pisar una recepción ya registrada."""
    diff = {}
    if not case["fecha_recepcion"]:
        diff["fecha_recepcion"] = {"antes": None, "ahora": fecha_llegada}
    if case["estado"] in ("Nuevo", "En gestión"):
        diff["estado"] = {"antes": case["estado"], "ahora": "Recibido"}
    return diff
