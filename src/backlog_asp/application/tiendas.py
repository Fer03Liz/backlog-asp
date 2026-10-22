"""Asocia las tiendas del backlog (códigos de CEVA) con el archivo de tiendas de Ingram (ASPs-Ingram-SHIPTO).

El backlog trae ship-to de CEVA (460xxx, 1xxxxxx) y el archivo de tiendas trae los de Ingram (1923xxx): no cruzan
por código. Se asocian por nombre:
- automáticamente cuando el nombre es el mismo (sin importar mayúsculas, espacios, "CC" ni el orden de las palabras);
- con sugerencia y confirmación del administrador en los demás casos (nunca se envía un correo por una suposición).
Con la asociación, el correo diario de la tienda toma el Service manager (para) y el Coordinador (copia).
"""
from ..domain.tiendas import palabras, parecido
from .auditoria import audit, now_iso

_ARCHIVO = "SELECT ship_to, name, email, cc_email FROM shipto WHERE email IS NOT NULL"
_ALIAS = "SELECT * FROM shipto_alias"
_BACKLOG = """SELECT ship_to, MAX(list_name) nombre, SUM(CASE WHEN source_status='abierto' THEN 1 ELSE 0 END) abiertos
              FROM cases WHERE ship_to IS NOT NULL GROUP BY ship_to ORDER BY nombre"""


def _datos(conn):
    archivo, alias, backlog = conn.select_many([(_ARCHIVO, ()), (_ALIAS, ()), (_BACKLOG, ())])
    usadas = {r["ship_to_archivo"] for r in alias}
    archivo = [{**dict(r), "palabras": palabras(r["name"]), "ya_asociada": r["ship_to"] in usadas} for r in archivo]
    return archivo, {r["ship_to"]: dict(r) for r in alias}, [dict(r) for r in backlog]


def sugerencias(conn) -> dict:
    archivo, alias, backlog = _datos(conn)
    directas = {a["ship_to"] for a in archivo}             # tienen correo propio (guardado en Correos)
    por_codigo = {a["ship_to"]: a for a in archivo}
    pendientes, asociadas = [], []
    for t in backlog:
        if t["ship_to"] in alias:
            a = alias[t["ship_to"]]
            dest = por_codigo.get(a["ship_to_archivo"], {})
            asociadas.append({**t, "ship_to_archivo": a["ship_to_archivo"], "nombre_archivo": dest.get("name"),
                              "email": dest.get("email"), "cc": dest.get("cc_email"), "origen": a["origen"]})
            continue
        if t["ship_to"] in directas:
            continue
        libres = [a for a in archivo if not a["ya_asociada"]] or archivo
        mejor = max(libres, key=lambda a: parecido(t["nombre"], a["name"]), default=None)
        puntaje = parecido(t["nombre"], mejor["name"]) if mejor else 0
        pendientes.append({**t, "sugerencia": {"ship_to": mejor["ship_to"], "nombre": mejor["name"],
                                               "puntaje": round(puntaje, 2)} if mejor and puntaje >= 0.6 else None})
    pendientes.sort(key=lambda x: (-(x["abiertos"] or 0), x["nombre"] or ""))
    return {"pendientes": pendientes, "asociadas": asociadas,
            "archivo": sorted(({"ship_to": a["ship_to"], "nombre": a["name"]} for a in archivo), key=lambda x: x["nombre"] or "")}


def asociar(conn, user, ship_to: str, ship_to_archivo: str, origen: str = "manual", validar: bool = True) -> None:
    if validar and not conn.execute("SELECT 1 x FROM shipto WHERE ship_to=? AND email IS NOT NULL", (ship_to_archivo,)).fetchone():
        raise ValueError("Esa tienda no está en el archivo de tiendas o no tiene correo.")
    conn.upsert("shipto_alias", {"ship_to": ship_to, "ship_to_archivo": ship_to_archivo, "origen": origen,
                                 "username": user["username"] if user else None, "at": now_iso()}, ("ship_to",), defer=True)
    audit(conn, user, "tienda_asociada", ship_to, {"archivo": ship_to_archivo, "origen": origen})


def quitar(conn, user, ship_to: str) -> None:
    conn.execute("DELETE FROM shipto_alias WHERE ship_to=?", (ship_to,))
    audit(conn, user, "tienda_desasociada", ship_to)


def asociar_automatico(conn, user) -> int:
    """Asocia solo cuando el nombre es el mismo (mismas palabras). Devuelve cuántas asoció."""
    archivo, alias, backlog = _datos(conn)
    directas = {a["ship_to"] for a in archivo}
    n = 0
    for t in backlog:
        if t["ship_to"] in alias or t["ship_to"] in directas:
            continue
        p = palabras(t["nombre"])
        iguales = [a for a in archivo if p and a["palabras"] == p]
        if len(iguales) == 1:                 # la tienda del archivo viene de la consulta: ya tiene correo
            asociar(conn, user, t["ship_to"], iguales[0]["ship_to"], "nombre idéntico", validar=False)
            n += 1
    return n
