"""Comparación de nombres de tiendas del backlog (códigos de CEVA) con el archivo de tiendas de Ingram."""
import re
import unicodedata

VACIAS = {"CC", "C.C.", "SA", "SAS", "S.A.S", "LTDA", "ESP", "DE", "LA", "EL", "DEL", "Y"}
MARCAS = {"ISHOP", "MAC", "CENTER", "COLOMBIA"}


def _limpias(nombre) -> list:
    s = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode().upper()
    return [w for w in re.split(r"[^A-Z0-9]+", s) if w and w not in VACIAS]


def palabras(nombre) -> frozenset:
    return frozenset(_limpias(nombre))


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


def parecido(nombre_a, nombre_b) -> float:
    """0 a 1. Solo compara tiendas de la misma marca y sin contar las palabras de marca."""
    pa, pb = _limpias(nombre_a), _limpias(nombre_b)
    if not pa or not pb or pa[0] != pb[0]:
        return 0.0
    a = [w for w in pa if w not in MARCAS]
    b = [w for w in pb if w not in MARCAS]
    if not a or not b:
        return 0.0
    return (_cubiertas(a, b) / len(a) + _cubiertas(b, a) / len(b)) / 2
