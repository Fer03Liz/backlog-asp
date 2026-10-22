"""Conexión a SQL Server con pymssql (pool de SQLAlchemy) y adaptador con la interfaz que usan los casos de uso.

- Las consultas se escriben con `?` y nombres de tabla sin esquema; aquí se pasan al estilo de pymssql y se
  califican con el esquema configurado (BACKLOG_DB_SCHEMA).
- El servidor está lejos (unos 120 ms por viaje): las escrituras masivas se acumulan con `defer` y se envían
  en lotes, y `select_many` trae varias consultas en un solo viaje.
- Los resultados se leen completos al ejecutar: pymssql no admite dos consultas abiertas en la misma conexión.
"""
import re
import threading

import pymssql
from pymssql import _mssql
from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool

from .. import config

TABLAS = ("users", "sessions", "settings", "loads", "cases", "case_history", "snapshots", "comments", "audit_log",
          "kgb", "prices", "parts", "shipto", "guide_arrivals", "holidays_extra", "emails_log", "open_orders",
          "open_order_history", "inventario", "inventario_history", "miami_purchases", "shipto_alias",
          "user_shipto", "solicitudes_fecha", "notas_leidas", "lotes")
_RX_TABLA = re.compile(r"\b(FROM|JOIN|INTO|UPDATE)\s+(" + "|".join(TABLAS) + r")\b", re.I)
LOTE_MAX = 400                     # sentencias por viaje en las escrituras acumuladas

IntegrityError = pymssql.IntegrityError

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            if not config.DATABASE_URL:
                raise RuntimeError("Falta SQL_SERVER_STRING_CONNECTION. Defínala en el archivo .env (ver .env.example).")
            _engine = create_engine(config.DATABASE_URL, poolclass=QueuePool, pool_size=config.DB_POOL_SIZE,
                                    max_overflow=config.DB_POOL_SIZE, pool_pre_ping=True, pool_recycle=1800,
                                    connect_args={"login_timeout": 20, "timeout": 600, "charset": "UTF-8"})

            @event.listens_for(_engine, "connect")
            def _al_conectar(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("SET NOCOUNT ON; SET LOCK_TIMEOUT 30000;")
                cur.close()
                dbapi_conn.commit()
    return _engine


def qualify(sql: str) -> str:
    return _RX_TABLA.sub(lambda m: f"{m.group(1)} [{config.DB_SCHEMA}].[{m.group(2)}]", sql)


def _valor(v):
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):    # números de numpy/pandas
        v = v.item()
    return v


def render(sql: str, params=()) -> str:
    """SQL final con los parámetros ya escapados por pymssql (el mismo mecanismo que usa cursor.execute)."""
    q = qualify(sql)
    if not params:
        return q
    out = _mssql.substitute_params(q.replace("%", "%%").replace("?", "%s"), tuple(_valor(p) for p in params))
    return out.decode("utf-8") if isinstance(out, bytes) else out       # pymssql 2.4 puede devolver bytes


class Row(dict):
    """Fila con acceso por nombre (r["col"]) y por posición (r[0]); dict(r) da un dict común."""
    __slots__ = ("_vals",)

    def __init__(self, cols, vals):
        super().__init__(zip(cols, vals))
        self._vals = vals

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _filas(cur) -> list:
    if not cur.description:
        return []
    cols = [c[0] for c in cur.description]
    return [Row(cols, v) for v in cur.fetchall()]


class Resultado:
    def __init__(self, rows, lastrowid=None):
        self._rows = rows
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class Conexion:
    def __init__(self, raw):
        self._raw = raw
        self._pend = []

    def execute(self, sql: str, params=()) -> Resultado:
        self.flush()
        q = render(sql, params)
        insert = q.lstrip()[:6].upper() == "INSERT"
        if insert:
            q += ";\nSELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS id"
        cur = self._raw.cursor()
        cur.execute(q)
        rows = _filas(cur)
        cur.close()
        if insert:
            return Resultado([], rows[0][0] if rows else None)
        return Resultado(rows)

    def defer(self, sql: str, params=()) -> None:
        """Escritura que no necesita respuesta: se envía en lote con la siguiente consulta o el commit."""
        self._pend.append(render(sql, params))
        if len(self._pend) >= LOTE_MAX:
            self.flush()

    def executemany(self, sql: str, seq) -> None:
        for p in seq:
            self.defer(sql, p)
        self.flush()

    def flush(self) -> None:
        if self._pend:
            lote, self._pend = self._pend, []
            cur = self._raw.cursor()
            cur.execute(";\n".join(lote))
            cur.close()

    def select_many(self, consultas) -> list:
        """[(sql, params), ...] -> [filas, ...] en un solo viaje al servidor."""
        self.flush()
        cur = self._raw.cursor()
        cur.execute(";\n".join(render(s, p) for s, p in consultas))
        out = [_filas(cur)]
        while cur.nextset():
            out.append(_filas(cur))
        cur.close()
        return out

    def upsert(self, table: str, row: dict, keys: tuple, defer: bool = False) -> None:
        """Inserta o reemplaza la fila con esa llave (equivale al INSERT OR REPLACE de SQLite)."""
        resto = [c for c in row if c not in keys]
        where = " AND ".join(f"[{k}]=?" for k in keys)
        cols = list(row)
        sets = ", ".join(f"[{c}]=?" for c in resto) or f"[{keys[0]}]=[{keys[0]}]"
        sql = (f"UPDATE {table} WITH (UPDLOCK, SERIALIZABLE) SET {sets} WHERE {where};\n"
               f"IF @@ROWCOUNT = 0 INSERT INTO {table}({', '.join(f'[{c}]' for c in cols)}) VALUES({', '.join('?' * len(cols))})")
        params = [row[c] for c in resto] + [row[k] for k in keys] + [row[c] for c in cols]
        (self.defer if defer else self.execute)(sql, params)

    def commit(self) -> None:
        self.flush()
        self._raw.commit()

    def rollback(self) -> None:
        self._pend = []
        self._raw.rollback()

    def close(self) -> None:
        self._pend = []
        self._raw.close()          # vuelve al pool, que deshace lo que no se confirmó


def connect() -> Conexion:
    return Conexion(engine().raw_connection())
