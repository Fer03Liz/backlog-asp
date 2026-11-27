"""Tabla `caso_calculado`: lo que la app calcula por caso (fechas, alertas, estado de la tienda), listo para Power BI.

Power BI lee SQL Server directamente y no puede repetir las reglas de la app (días hábiles, cruce con KGB,
inventario, alertas). Por eso la app las deja escritas una fila por caso. Se reconstruye completa:
- poco después de cada carga o cambio (con espera de unos segundos para juntar cambios seguidos),
- al arrancar la app, y
- con `poetry run backlog-refrescar` (para una tarea programada, justo antes de refrescar Power BI).
Solo cuentan los casos abiertos y los cerrados en los últimos 45 días.
"""
import threading

from ..domain.calendario import today
from ..domain.casos import estado_tienda
from ..infrastructure import database
from . import contexto
from .auditoria import now_iso

ESPERA_SEGUNDOS = 15
_COLUMNAS = ["key_notif", "notificacion", "ship_to", "tienda", "parte", "descripcion", "equipo", "creado", "source_status",
             "pendiente", "estado", "estado_tienda", "fecha_tienda", "fecha_tienda_tipo", "opcion", "eta_apple",
             "llegada_estimada", "llegada_fuente", "despacho_posible", "fecha_recepcion", "fecha_despacho",
             "novedad_despacho", "limite_fr", "dias_limite", "edad", "nivel", "alertas", "alertas_texto", "prioridad",
             "super_asp", "propuesta", "aprobacion_kam", "tienda_email", "cerrado_el", "calculado_at"]
_timer = None
_lock = threading.Lock()


def _fila(c: dict, hoy: str, ahora: str) -> tuple:
    est, fecha, tipo = estado_tienda(c, hoy)
    textos = " | ".join(a["text"] for a in c["alerts"])
    return (c["key_notif"], f"{c['notifctn']}-{int(c['line_item'] or 0):02d}", c["ship_to"], c["list_name"], c["assembly"],
            c["description"], c.get("product_description") or c.get("material_desc"), c["created_on"], c["source_status"],
            int(bool(c["pendiente"])), c["estado"], est, fecha, tipo, c["option_short"], c["eta_jenny"], c["llegada"],
            c["llegada_fuente"], c["despacho_posible"], c["fecha_recepcion"], c["fecha_despacho"], c["novedad_despacho"],
            c["limite_fr"], c["dias_limite"], c["edad"], c["nivel"], len(c["alerts"]), textos or None, c["prioridad"],
            int(bool(c["super_asp"])), c["propuesta"], c["aprobacion_kam"], c["tienda_email"], c["closed_on"], ahora)


def refrescar(conn) -> int:
    """Reconstruye la tabla en una transacción. Devuelve las filas escritas (-1 si otro proceso ya la está reconstruyendo)."""
    lock = conn.execute("DECLARE @r INT; EXEC @r = sp_getapplock @Resource=N'caso_calculado', @LockMode=N'Exclusive', "
                        "@LockOwner=N'Transaction', @LockTimeout=0; SELECT @r AS r").fetchone()
    if lock is None or lock["r"] < 0:
        conn.rollback()
        return -1
    ctx = contexto.Context(conn)
    hoy, ahora = ctx.today.isoformat(), now_iso()
    filas = [_fila(c, hoy, ahora) for c in contexto.load_cases(conn, ctx, include_closed=True, closed_days=45)]
    conn.execute("DELETE FROM caso_calculado")
    sql = f"INSERT INTO caso_calculado({', '.join(f'[{c}]' for c in _COLUMNAS)}) VALUES({', '.join('?' * len(_COLUMNAS))})"
    conn.executemany(sql, filas)
    conn.commit()
    return len(filas)


def _ejecutar() -> None:
    global _timer
    with _lock:
        _timer = None
    conn = database.connect()
    try:
        refrescar(conn)
    except Exception:                         # no debe tumbar la app: el próximo cambio o el CLI lo vuelve a intentar
        conn.rollback()
    finally:
        conn.close()


def programar() -> None:
    """Pide una reconstrucción en unos segundos; varias peticiones seguidas se juntan en una sola."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(ESPERA_SEGUNDOS, _ejecutar)
        _timer.daemon = True
        _timer.start()
