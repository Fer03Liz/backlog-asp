"""Reglas de Open Orders de Apple.

Regla de Open Orders (verificada con los archivos del 5 al 13 de octubre de 2026):
una línea (orden + ítem) que sigue en Open Orders todavía no ha salido de Apple. Cuando Apple la asigna,
desaparece de Open Orders y aparece en el KGB con la misma orden e ítem, primero sin guía ("Ship Backlog")
y luego con guía y EDD. Que la orden ya tenga otras líneas en el KGB no dice nada de las que siguen pendientes.
"""
import re
from datetime import date, timedelta

MESES = {"jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "jun": 6, "jul": 7,
         "aug": 8, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12, "dic": 12}
RE_RECOVERY = re.compile(r"(?:Recovery Date|End date)\s*:\s*(\d{4})-([A-Za-z]+)-(\d{1,2})", re.I)
SIN_ABASTO = ("OH", "FG", "Substitute Upgrade or/and Color change")   # soluciones que no esperan esta parte


def date_from_name(name: str):
    m = re.search(r"(20\d{2})[_\-.](\d{2})[_\-.](\d{2})", name)          # 2026_09_28
    if m:
        y, mo, da = m.groups()
    else:
        m = re.search(r"(\d{2})[_\-.](\d{2})[_\-.](20\d{2})", name)      # 09_29_2026
        if not m:
            return None
        mo, da, y = m.groups()
    try:
        return date(int(y), int(mo), int(da)).isoformat()
    except ValueError:
        return None


def fecha_nombre(name: str):
    """'Colombia-Open Orders Report-Oct.13.2026.xlsx' -> 2026-10-13. Si no, usa el formato de siempre."""
    for m in re.finditer(r"([A-Za-z]{3})[a-z]*[._\- ]?(\d{1,2})[._\-, ]+(20\d{2})", name):
        mes = MESES.get(m.group(1).lower())
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(2))).isoformat()
            except ValueError:
                pass
    return date_from_name(name)


def _proximo_mar_jue(x: date) -> date:
    y = x + timedelta(days=1)
    while y.weekday() not in (1, 3):
        y += timedelta(days=1)
    return y


def parse_recovery(texto, status, file_date: date):
    """Devuelve (fecha de recovery, salida estimada de Apple). Recovery es cuando Apple tendrá stock."""
    t = (texto or "").strip()
    m = RE_RECOVERY.search(t)
    if m:
        mes = MESES.get(m.group(2)[:3].lower())
        try:
            rec = date(int(m.group(1)), mes, int(m.group(3))) if mes else None
        except ValueError:
            rec = None
        if rec:
            return rec, max(rec, file_date)       # si ya pasó y sigue aquí, lo más pronto es hoy
    st = (status or "").strip().lower()
    if "tues/thur" in t.lower() or (st == "shipment pending" and not t):
        return None, _proximo_mar_jue(file_date)
    return None, None


def tipo_orden(cust_po) -> str:
    s = (cust_po or "").strip().upper()
    if s.startswith("CHANNELFOC_NPI"):
        return "NPI"
    if s.startswith("CHANNELFOC"):
        return "Channel FOC"
    if s.isdigit():
        return "PO"
    return "Otra"


def pct(a, b):
    return round(100 * a / b, 1) if b else None
