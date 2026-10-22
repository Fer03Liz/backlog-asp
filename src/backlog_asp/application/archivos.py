"""Lectura de los archivos que se cargan (xlsx/xlsm/csv) y conversión de celdas."""
import io
import math
import re
from datetime import date, datetime

import pandas as pd

from ..domain.errores import LoadError


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip()) or v is pd.NaT


def to_str(v):
    if _empty(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def to_date(v):
    if _empty(v):
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return pd.to_datetime(str(v), dayfirst=False).date().isoformat()
    except (ValueError, TypeError):
        return None


def to_num(v):
    if _empty(v):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def read_table(data: bytes, name: str) -> dict:
    """Devuelve {hoja: DataFrame}. Soporta xlsx/xlsm y csv."""
    lower = name.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl", dtype=object)
        if lower.endswith(".csv"):
            return {"csv": pd.read_csv(io.BytesIO(data), dtype=str, sep=None, engine="python", encoding="utf-8-sig")}
    except Exception as exc:  # archivo dañado o con formato inesperado
        raise LoadError(f"No se pudo leer el archivo: {exc.__class__.__name__}.") from exc
    raise LoadError("Formato no admitido. Use .xlsx o .csv.")


def read_raw(data: bytes, name: str) -> pd.DataFrame:
    """Primera hoja sin encabezado, para archivos exportados de tablas dinámicas."""
    lower = name.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            return pd.read_excel(io.BytesIO(data), sheet_name=0, header=None, engine="openpyxl", dtype=object)
        if lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(data), header=None, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as exc:
        raise LoadError(f"No se pudo leer el archivo: {exc.__class__.__name__}.") from exc
    raise LoadError("Formato no admitido. Use .xlsx o .csv.")


def columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def require(df, required, what):
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise LoadError(f"Al archivo de {what} le faltan columnas: {', '.join(missing)}.")


def find(df, *keys):
    for c in df.columns:
        low = c.lower()
        if any(k in low for k in keys):
            return c
    return None


def pick(df, exact, *contains):
    for c in df.columns:
        if c.lower() in exact:
            return c
    return find(df, *contains) if contains else None
