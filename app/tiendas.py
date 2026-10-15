"""Asocia las tiendas del backlog (códigos de CEVA) con el archivo de tiendas de Ingram (ASPs-Ingram-SHIPTO).

El backlog trae ship-to de CEVA (460xxx, 1xxxxxx) y el archivo de tiendas trae los de Ingram (1923xxx): no cruzan
por código. Se asocian por nombre:
- automáticamente cuando el nombre es el mismo (sin importar mayúsculas, espacios, "CC" ni el orden de las palabras);
- con sugerencia y confirmación del administrador en los demás casos (nunca se envía un correo por una suposición).
Con la asociación, el correo diario de la tienda toma el Service manager (para) y el Coordinador (copia).
"""
import re
import unicodedata

from . import db

SCHEMA_TIENDAS = """
CREATE TABLE IF NOT EXISTS shipto_alias(
  ship_to TEXT PRIMARY KEY, ship_to_archivo TEXT NOT NULL, origen TEXT NOT NULL,
  username TEXT, at TEXT NOT NULL
);
"""
VACIAS = {"CC", "C.C.", "SA", "SAS", "S.A.S", "LTDA", "ESP", "DE", "LA", "EL", "DEL", "Y"}


def init_db() -> None:
    """Crea la tabla y asocia al arrancar las tiendas con el mismo nombre (si ya hay archivo de tiendas)."""
    conn = db.connect()
    try:
        conn.executescript(SCHEMA_TIENDAS)
        if conn.execute("SELECT 1 FROM shipto LIMIT 1").fetchone():
            asociar_automatico(conn, None)
        conn.commit()
    finally:
        conn.close()


def _palabras(nombre) -> frozenset:
    s = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode().upper()
    return frozenset(w for w in re.split(r"[^A-Z0-9]+", s) if w and w not in VACIAS)


MARCAS = {"ISHOP", "MAC", "CENTER", "COLOMBIA"}


def _lev1(a: str, b: str) -> bool:
    """¿Difieren en a lo sumo una letra (cambiada, sobrante o faltante)?"""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    corto, largo = (a, b) if len(a) < len(b) else (b, a)
    return any(largo[:i] + largo[i + 1:] == corto for i in range(len(largo)))


def _igual(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):     # BARRANQ ~ BARRANQUILLA
        return True
    return min(len(a), len(b)) >= 5 and _lev1(a, b)                          # NUESTRO ~ NUESTO


def _cubiertas(a: list, b: list) -> int:
    """Cuántas palabras de a aparecen en b (también si dos palabras seguidas de b forman una: SANTA FE ~ SANTAFE)."""
    unidas = [x + y for x, y in zip(b, b[1:])]
    n = 0
    for i, w in enumerate(a):
        par = [a[i - 1] + w] if i > 0 else []
        par += [w + a[i + 1]] if i + 1 < len(a) else []
        if any(_igual(w, x) for x in b) or any(w == u for u in unidas) or any(p == x for p in par for x in b):
            n += 1
    return n


def _parecido(nombre_a, nombre_b) -> float:
    """0 a 1. Solo compara tiendas de la misma marca y sin contar las palabras de marca."""
    def partes(n):
        s = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode().upper()
        return [w for w in re.split(r"[^A-Z0-9]+", s) if w and w not in VACIAS]
    pa, pb = partes(nombre_a), partes(nombre_b)
    if not pa or not pb or pa[0] != pb[0]:
        return 0.0
    a = [w for w in pa if w not in MARCAS]
    b = [w for w in pb if w not in MARCAS]
    if not a or not b:
        return 0.0
    return (_cubiertas(a, b) / len(a) + _cubiertas(b, a) / len(b)) / 2


def cargar_contexto(ctx, conn) -> None:
    """Agrega al contexto los datos de la tienda de Ingram asociada a cada ship-to del backlog."""
    try:
        for r in conn.execute("SELECT ship_to, ship_to_archivo FROM shipto_alias"):
            if r["ship_to"] not in ctx.shipto and r["ship_to_archivo"] in ctx.shipto:
                ctx.shipto[r["ship_to"]] = {**ctx.shipto[r["ship_to_archivo"]], "ship_to": r["ship_to"],
                                            "asociada_a": r["ship_to_archivo"]}
    except Exception:          # tabla aún no creada
        pass


def _archivo(conn) -> list:
    """Tiendas del archivo de Ingram (las que tienen correo)."""
    alias = {r["ship_to_archivo"] for r in conn.execute("SELECT ship_to_archivo FROM shipto_alias")}
    filas = conn.execute("SELECT ship_to, name, email, cc_email FROM shipto WHERE email IS NOT NULL")
    return [{**dict(r), "palabras": _palabras(r["name"]), "ya_asociada": r["ship_to"] in alias} for r in filas]


def _backlog(conn) -> list:
    return [dict(r) for r in conn.execute(
        """SELECT ship_to, MAX(list_name) nombre, SUM(source_status='abierto') abiertos FROM cases
           WHERE ship_to IS NOT NULL GROUP BY ship_to ORDER BY nombre""")]


def sugerencias(conn) -> dict:
    archivo = _archivo(conn)
    directas = {r["ship_to"] for r in conn.execute("SELECT ship_to FROM shipto WHERE email IS NOT NULL")}
    alias = {r["ship_to"]: dict(r) for r in conn.execute("SELECT * FROM shipto_alias")}
    por_codigo = {a["ship_to"]: a for a in archivo}
    pendientes, asociadas = [], []
    for t in _backlog(conn):
        if t["ship_to"] in alias:
            a = alias[t["ship_to"]]
            dest = por_codigo.get(a["ship_to_archivo"], {})
            asociadas.append({**t, "ship_to_archivo": a["ship_to_archivo"], "nombre_archivo": dest.get("name"),
                              "email": dest.get("email"), "cc": dest.get("cc_email"), "origen": a["origen"]})
            continue
        if t["ship_to"] in directas:
            continue                     # tiene correo propio (guardado en Correos)
        libres = [a for a in archivo if not a["ya_asociada"]] or archivo
        mejor = max(libres, key=lambda a: _parecido(t["nombre"], a["name"]), default=None)
        puntaje = _parecido(t["nombre"], mejor["name"]) if mejor else 0
        pendientes.append({**t, "sugerencia": {"ship_to": mejor["ship_to"], "nombre": mejor["name"],
                                               "puntaje": round(puntaje, 2)} if mejor and puntaje >= 0.6 else None})
    pendientes.sort(key=lambda x: (-(x["abiertos"] or 0), x["nombre"] or ""))
    return {"pendientes": pendientes, "asociadas": asociadas,
            "archivo": sorted(({"ship_to": a["ship_to"], "nombre": a["name"]} for a in archivo), key=lambda x: x["nombre"] or "")}


def asociar(conn, user, ship_to: str, ship_to_archivo: str, origen: str = "manual") -> None:
    if not conn.execute("SELECT 1 FROM shipto WHERE ship_to=? AND email IS NOT NULL", (ship_to_archivo,)).fetchone():
        raise ValueError("Esa tienda no está en el archivo de tiendas o no tiene correo.")
    conn.execute("INSERT OR REPLACE INTO shipto_alias(ship_to, ship_to_archivo, origen, username, at) VALUES(?,?,?,?,?)",
                 (ship_to, ship_to_archivo, origen, user["username"] if user else None, db.now_iso()))
    db.audit(conn, user, "tienda_asociada", ship_to, {"archivo": ship_to_archivo, "origen": origen})


def quitar(conn, user, ship_to: str) -> None:
    conn.execute("DELETE FROM shipto_alias WHERE ship_to=?", (ship_to,))
    db.audit(conn, user, "tienda_desasociada", ship_to)


def asociar_automatico(conn, user) -> int:
    """Asocia solo cuando el nombre es el mismo (mismas palabras). Devuelve cuántas asoció."""
    archivo = _archivo(conn)
    directas = {r["ship_to"] for r in conn.execute("SELECT ship_to FROM shipto WHERE email IS NOT NULL")}
    ya = {r["ship_to"] for r in conn.execute("SELECT ship_to FROM shipto_alias")}
    n = 0
    for t in _backlog(conn):
        if t["ship_to"] in ya or t["ship_to"] in directas:
            continue
        p = _palabras(t["nombre"])
        iguales = [a for a in archivo if p and a["palabras"] == p]
        if len(iguales) == 1:
            asociar(conn, user, t["ship_to"], iguales[0]["ship_to"], "nombre idéntico")
            n += 1
    return n
