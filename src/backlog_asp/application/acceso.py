"""Roles internos y qué puede hacer cada uno.

- admin: todo, incluida la administración (usuarios, parámetros, festivos, bitácora, cierre de fase).
- supervisor (bodega, logística y abastecimiento, y atención al cliente): gestiona casos, registra recepción y despacho,
  aprueba solicitudes de cambio de fechas protegidas y deshace tandas ajenas. Sin administración.
- comprador: gestiona casos y registra recepción y despacho, compras en Miami, Open Orders, precios y Parts Data.
- atencion (atención al cliente): ve todo, comenta, registra correos y actualiza las tiendas.
La ACM y las tiendas ya no entran a la app: sus vistas se publican en Power BI desde la tabla `caso_calculado`.
El control está en el servidor; la interfaz solo oculta lo que el rol no puede usar.
"""
ROLES_INTERNOS = ("admin", "supervisor", "comprador", "atencion")
NOMBRES = {"admin": "Administrador", "supervisor": "Supervisor", "comprador": "Comprador", "atencion": "Atención al cliente"}

EDITORES = ("admin", "supervisor", "comprador")        # casos, recepción, despacho, guías, compras en Miami, tandas
APROBADORES = ("admin", "supervisor")                   # fechas protegidas y solicitudes de cambio
CORREOS = ("admin", "supervisor", "atencion")           # registrar correos y actualizar datos de tiendas
CARGAS = {                                              # quién carga cada archivo
    "backlog": ("admin", "supervisor"),
    "kgb": ("admin", "supervisor", "comprador"),
    "openorders": ("admin", "supervisor", "comprador"),
    "inventario": ("admin", "supervisor"),
    "precios": ("admin", "comprador"),
    "partes": ("admin", "comprador"),
    "shipto": ("admin", "supervisor", "atencion"),
}
INTERNOS = ROLES_INTERNOS


def permitido(role: str, method: str, path: str) -> bool:
    return role in ROLES_INTERNOS


def puede_cargar(role: str, kind: str) -> bool:
    return role in CARGAS.get(kind, ())


def perfil(role: str) -> dict:
    """Lo que cada rol puede hacer en pantalla (el servidor lo vuelve a validar en cada petición)."""
    return {"pestanas": None, "ver_precios": True, "ver_miami": True,
            "puede_editar": role in EDITORES, "puede_comentar": role in ROLES_INTERNOS,
            "puede_aprobar": role in APROBADORES, "puede_correos": role in CORREOS,
            "puede_administrar": role == "admin", "cargas": [k for k, r in CARGAS.items() if role in r]}


def comentarios(conn, user, key) -> list:
    sql = """SELECT m.id, m.username, m.at, m.text, m.visibilidad, u.role autor_rol
             FROM comments m LEFT JOIN users u ON u.id = m.user_id WHERE m.key_notif=? ORDER BY m.id DESC"""
    return [dict(r) for r in conn.execute(sql, (key,))]
