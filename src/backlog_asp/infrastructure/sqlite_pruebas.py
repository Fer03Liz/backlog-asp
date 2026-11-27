"""Base SQLite para probar la app en este equipo sin SQL Server (BACKLOG_DB_ENGINE=sqlite). Ver PRUEBAS.md.

Es solo para pruebas locales: el servidor sigue con SQL Server y este módulo no cambia nada de ese modo.

- La app escribe sus consultas en T-SQL (TOP, [corchetes], LEFT...). Aquí se traducen a SQLite al ejecutarlas: se
  prueba el mismo código que va al servidor.
- Al revés, una consulta con algo que solo existe en SQLite (LIMIT, ||, ON CONFLICT...) se rechaza con un error
  claro, porque funcionaría aquí y fallaría en SQL Server. Lo mismo con el T-SQL que este módulo no sabe traducir.
- El esquema sale de las mismas definiciones de esquema.py (tablas, índices, columnas nuevas, triggers y parámetros).
  Si una rama cambia una tabla, la base de pruebas se ajusta sola y conserva los datos.
- Se parece a SQL Server en lo que importa: textos sin distinguir mayúsculas (COLLATE NOCASE), llaves foráneas
  activas, bitácora y comentarios de solo lectura, espera de 30 s por bloqueos y lecturas sin transacción hasta la
  primera escritura (como READ COMMITTED).
- Se niega a abrir la base de la versión anterior (data/backlog.db) y los respaldos.
"""
import math
import os
import re
import sqlite3
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pymssql

from .. import config
from .database import Resultado, Row

ESPERA_BLOQUEO_S = 30                 # igual que SET LOCK_TIMEOUT 30000 en SQL Server
_META = "_pruebas_esquema"            # definición con la que se creó cada tabla (para ajustarla si cambia)


class SQLNoPortable(RuntimeError):
    """La consulta no correría igual en SQL Server, o el modo pruebas no sabe traducirla."""


# ---------------------------------------------------------------- archivo de la base y protecciones
def ruta_base() -> Path:
    """Archivo de la base de pruebas. Los esquemas de las pruebas automáticas (backlog_test_…) van a la carpeta temporal."""
    if config.DB_SCHEMA.startswith("backlog_test"):
        return Path(tempfile.gettempdir()) / f"{config.DB_SCHEMA}.db"
    return config.SQLITE_PATH


def _dentro(ruta: Path, carpeta: Path) -> bool:
    r, c = os.path.normcase(str(ruta)), os.path.normcase(str(carpeta))
    return r == c or r.startswith(c.rstrip("\\/") + os.sep)


def validar_ruta(ruta) -> Path:
    """La base de pruebas nunca puede ser la base anterior (data/backlog.db) ni un respaldo."""
    ruta = Path(ruta).resolve()
    data = (config.PROJECT_DIR / "data").resolve()
    if os.path.normcase(str(ruta)) == os.path.normcase(str(data / "backlog.db")):
        raise RuntimeError(f"{ruta} es la base de la versión anterior: el modo pruebas no la abre. "
                           "Use otra ruta en BACKLOG_SQLITE_PATH (por defecto data/pruebas/backlog_pruebas.db).")
    protegidas = [data / "backups", *(p for p in data.glob("respaldo_*") if p.is_dir())]
    if any(_dentro(ruta, c) for c in protegidas):
        raise RuntimeError(f"{ruta} está en una carpeta de respaldos: el modo pruebas no la abre.")
    if ruta.is_dir():
        raise RuntimeError(f"BACKLOG_SQLITE_PATH apunta a una carpeta ({ruta}); debe ser un archivo .db.")
    return ruta


# ---------------------------------------------------------------- valores (igual que los escribe pymssql)
def _valor(v):
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):     # números de numpy/pandas
        v = v.item()
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise SQLNoPortable("Se envió NaN o infinito como parámetro: SQL Server lo rechaza (pymssql lo escribe como "
                            "'nan'). Conviértalo a None antes de guardarlo.")
    if isinstance(v, datetime):                                     # pymssql: 'AAAA-MM-DD HH:MM:SS.mmm'
        try:
            return v.strftime("%Y-%m-%d %H:%M:%S.") + f"{v.microsecond // 1000:03d}"
        except ValueError:                                          # pd.NaT
            raise SQLNoPortable("Se envió NaT (fecha vacía de pandas) como parámetro: conviértala a None.") from None
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.strftime("%H:%M:%S.%f")
    if isinstance(v, Decimal):
        return str(v)
    return v


# ---------------------------------------------------------------- traducción T-SQL -> SQLite
_LITERALES = re.compile(r"""(?P<cadena>(?:(?<![\w$@#])[Nn])?'(?:[^']|'')*')
                          |(?P<corchete>\[[^\]]*\])
                          |(?P<comillas>"[^"]*")
                          |(?P<acento>`[^`]*`)
                          |(?P<comentario>--[^\n]*|/\*.*?\*/)""", re.S | re.X)
_MARCA = re.compile("\x00(\\d+)\x00")           # literal enmascarado
_PARAM = re.compile("\x01(\\d+)\x01")           # parámetro ? numerado

_SOLO_SQLITE = [
    (r"\bLIMIT\b", "LIMIT no existe en SQL Server: use SELECT TOP n (el modo pruebas lo traduce)."),
    (r"\bOFFSET\b(?!\s*\S+\s+ROWS?\b)", "En SQL Server OFFSET va con ROWS: use OFFSET n ROWS FETCH NEXT m ROWS ONLY."),
    (r"\|\|", "SQL Server no concatena con ||: arme el texto en Python o use CONCAT()."),
    (r"\bINSERT\s+OR\b|\bREPLACE\s+INTO\b|\bON\s+CONFLICT\b",
     "INSERT OR / REPLACE INTO / ON CONFLICT son de SQLite: use conn.upsert(tabla, fila, llaves)."),
    (r"\bRETURNING\b", "RETURNING no existe en SQL Server: use conn.execute(\"INSERT ...\").lastrowid."),
    (r"\bPRAGMA\b|\bsqlite_\w+|\bAUTOINCREMENT\b|\bWITHOUT\s+ROWID\b|\b_?rowid_?\b",
     "«{0}» es propio de SQLite y no existe en SQL Server."),
    (r"\b(?:GLOB|REGEXP)\b", "GLOB y REGEXP no existen en SQL Server: use LIKE."),
    (r"\b(?:TRUE|FALSE)\b", "SQL Server no tiene TRUE ni FALSE: use 1 y 0."),
    (r"==", "SQL Server no acepta ==: use =."),
    (r"\b(?:NOTNULL|ISNULL)\b(?!\s*\()", "Use IS NULL o IS NOT NULL: ISNULL y NOTNULL sin paréntesis son de SQLite."),
    (r"(?<!\bAS )\b(ifnull|substr|instr|length|strftime|julianday|unixepoch|date|datetime|time|group_concat|printf|"
     r"typeof|total|random|randomblob|hex|quote|zeroblob|likely|unlikely|changes|total_changes|last_insert_rowid|"
     r"json_extract|json_each|json_group_array|json_group_object)\s*\(",
     "La función {0}() es de SQLite y no existe en SQL Server."),
]
_SOLO_SQLITE = [(re.compile(rx, re.I), msg) for rx, msg in _SOLO_SQLITE]

_SIN_TRADUCCION = re.compile(r"""@@\w+|\bSCOPE_IDENTITY\s*\(|\bIDENT_CURRENT\s*\(|\bOUTPUT\s+(?:INSERTED|DELETED)\b
    |^\s*(?:MERGE|DECLARE|EXEC|EXECUTE|IF|WHILE|BEGIN|PRINT|RAISERROR|THROW|USE|GO|WAITFOR)\b
    |\b(?:GETDATE|GETUTCDATE|SYSDATETIME|SYSUTCDATETIME|SYSDATETIMEOFFSET|DATEADD|DATEDIFF|DATEDIFF_BIG|DATEPART|DATENAME
        |DATEFROMPARTS|EOMONTH|CONVERT|TRY_CONVERT|TRY_CAST|TRY_PARSE|PARSE|FORMAT|STRING_AGG|STRING_SPLIT|OPENJSON
        |JSON_VALUE|JSON_QUERY|NEWID|NEWSEQUENTIALID|OBJECT_ID|COL_LENGTH|SCHEMA_ID|DB_NAME|SUSER_SNAME|STUFF|REPLICATE
        |PATINDEX|SPACE|TRANSLATE|STR|CHOOSE)\s*\(
    |\bCURRENT_TIMESTAMP\b|\bsys\.\w+|\bINFORMATION_SCHEMA\.|\b(?:CROSS|OUTER)\s+APPLY\b|\b(?:UN)?PIVOT\b
    |\#\w+|\bFOR\s+(?:XML|JSON)\b|\bWITH\s*\(|\bTOP\b""", re.I | re.X)

_SET_SIN_EFECTO = re.compile(r"^\s*SET\s+(?:NOCOUNT|LOCK_TIMEOUT|XACT_ABORT|IDENTITY_INSERT|ANSI_\w+|QUOTED_IDENTIFIER"
                             r"|ARITHABORT|CONCAT_NULL_YIELDS_NULL|DATEFORMAT|LANGUAGE|DEADLOCK_PRIORITY"
                             r"|TRANSACTION\s+ISOLATION\s+LEVEL)\b", re.I)
_TOP = re.compile(r"^(\s*SELECT\s+(?:DISTINCT\s+)?)TOP\s*(?:\(\s*(\d+|\x01\d+\x01)\s*\)|(\d+))(\s+PERCENT\b|\s+WITH\s+TIES\b)?",
                  re.I)
_OFFSET = re.compile(r"\bOFFSET\s+(\d+|\x01\d+\x01)\s+ROWS?\s+FETCH\s+(?:NEXT|FIRST)\s+(\d+|\x01\d+\x01)\s+ROWS?\s+ONLY\b",
                     re.I)
_PISTA = r"(?:NOLOCK|READUNCOMMITTED|READCOMMITTED|REPEATABLEREAD|SERIALIZABLE|HOLDLOCK|UPDLOCK|XLOCK|ROWLOCK|PAGLOCK|TABLOCKX?|READPAST|NOWAIT)"
_PISTAS = re.compile(rf"\bWITH\s*\(\s*{_PISTA}(?:\s*,\s*{_PISTA})*\s*\)|\(\s*NOLOCK\s*\)", re.I)
_COLLATE = re.compile(r"\bCOLLATE\s+(\w+)", re.I)
_ESCRITURA = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE)\b|^\s*WITH\b.*\b(?:INSERT|UPDATE|DELETE)\b",
                        re.I | re.S)
_INSERT_EN = re.compile(r"^\s*INSERT\s+INTO\s+\[?(\w+)\]?", re.I)
_TRUNCATE = re.compile(r"^\s*TRUNCATE\s+TABLE\b", re.I)


class Sentencia:
    __slots__ = ("sql", "orden", "tipo", "tabla_insert")

    def __init__(self, sql, orden, tipo, tabla_insert=None):
        self.sql, self.orden, self.tipo, self.tabla_insert = sql, orden, tipo, tabla_insert


def _enmascarar(sql: str):
    lits = []

    def guardar(m):
        txt = m.group(0)
        if m.lastgroup == "cadena" and txt[0] in "Nn":        # N'texto' -> 'texto'
            txt = txt[1:]
        lits.append((m.lastgroup, txt))
        return f"\x00{len(lits) - 1}\x00"

    codigo = _LITERALES.sub(guardar, sql)
    n = iter(range(10 ** 6))
    codigo = re.sub(r"\?", lambda _m: f"\x01{next(n)}\x01", codigo)
    return codigo, lits


def _parentesis(codigo: str, abre: int) -> int:
    """Posición del paréntesis que cierra el que está en `abre`."""
    nivel = 0
    for i in range(abre, len(codigo)):
        if codigo[i] == "(":
            nivel += 1
        elif codigo[i] == ")":
            nivel -= 1
            if nivel == 0:
                return i
    return len(codigo)


def _revisar(codigo: str, lits: list, original: str) -> None:
    """Rechaza lo que solo funciona en SQLite (fallaría en SQL Server) y el T-SQL que aquí no se traduce."""
    def error(msg):
        corto = " ".join(original.split())
        raise SQLNoPortable(f"{msg}\n  Consulta: {corto[:300]}")

    for tipo, txt in lits:
        if tipo == "comillas":
            error(f"Comillas dobles ({txt}): use [corchetes] para nombres y 'comillas simples' para textos. "
                  "Con pymssql, SQL Server no las trata como en SQLite.")
        if tipo == "acento":
            error(f"Acentos graves ({txt}) son de SQLite/MySQL: use [corchetes].")
    for rx, msg in _SOLO_SQLITE:
        m = rx.search(codigo)
        if m:
            error(msg.format(*(m.groups() or (m.group(0),))))
    for m in re.finditer(r"\b(MIN|MAX)\s*\(", codigo, re.I):
        abre = m.end() - 1
        dentro, nivel = codigo[abre + 1:_parentesis(codigo, abre)], 0
        for ch in dentro:
            nivel += ch == "("
            nivel -= ch == ")"
            if ch == "," and nivel == 0:
                error(f"{m.group(1).upper()}() con varios argumentos es de SQLite: en SQL Server use CASE.")
    cadenas = {str(i) for i, (t, _) in enumerate(lits) if t == "cadena"}
    for m in re.finditer("\x00(\\d+)\x00\\s*\\+|\\+\\s*\x00(\\d+)\x00", codigo):
        if (m.group(1) or m.group(2)) in cadenas:
            error("Concatenar textos con + no se puede probar en SQLite (allí + suma números). "
                  "Arme el texto en Python o use CONCAT().")


def _no_traducido(que: str, original: str):
    corto = " ".join(original.split())
    raise SQLNoPortable(
        f"La consulta usa «{que}», válido en SQL Server pero que el modo pruebas (SQLite) no traduce, así que aquí "
        f"no se puede probar. Si lo necesita, agregue la traducción en infrastructure/sqlite_pruebas.py."
        f"\n  Consulta: {corto[:300]}")


def _traducir_una(codigo: str, original: str) -> str:
    if _TRUNCATE.match(codigo):
        codigo = _TRUNCATE.sub("DELETE FROM", codigo, count=1)
    codigo = _PISTAS.sub("", codigo)
    m = _TOP.match(codigo)
    if m:
        if m.group(4):
            _no_traducido("TOP ... PERCENT / WITH TIES", original)
        if re.search(r"\bUNION\b|\bOFFSET\b", codigo, re.I):
            _no_traducido("TOP junto con UNION u OFFSET", original)
        n = m.group(2) or m.group(3)
        codigo = m.group(1) + codigo[m.end():].rstrip() + f" LIMIT {n}"
    m = _OFFSET.search(codigo)
    if m:
        codigo = codigo[:m.start()] + f"LIMIT {m.group(2)} OFFSET {m.group(1)}" + codigo[m.end():]
    m = _SIN_TRADUCCION.search(codigo)                    # lo que quede sin traducir (TOP en subconsultas, etc.)
    if m:
        _no_traducido(m.group(0).strip(), original)
    codigo = re.sub(r"\bISNULL\s*\(", "ifnull(", codigo, flags=re.I)
    codigo = _COLLATE.sub(lambda c: "COLLATE " + ("BINARY" if "_CS" in c.group(1).upper() else "NOCASE"), codigo)
    return codigo


@lru_cache(maxsize=4096)
def traducir(sql: str) -> tuple:
    """T-SQL de la app -> sentencias SQLite con el orden de sus parámetros. Cada texto se traduce una vez."""
    codigo, lits = _enmascarar(sql)
    _revisar(codigo, lits, sql)
    if re.search(r"\bsp_getapplock\b", codigo, re.I):    # bloqueo de aplicación: aquí lo da BEGIN IMMEDIATE
        return (Sentencia(None, (), "bloqueo"),)
    if re.search(r"\bsp_releaseapplock\b", codigo, re.I):
        return (Sentencia(None, (), "nada"),)
    out = []
    for parte in codigo.split(";"):
        if not parte.strip():
            continue
        if _SET_SIN_EFECTO.match(parte):
            out.append(Sentencia(None, (), "nada"))
            continue
        trad = _traducir_una(parte, sql)
        orden = tuple(int(x) for x in _PARAM.findall(trad))
        trad = _PARAM.sub("?", trad)
        trad = _MARCA.sub(lambda m: lits[int(m.group(1))][1], trad).strip()
        tipo = "escritura" if _ESCRITURA.match(trad) else "lectura"
        ins = _INSERT_EN.match(trad)
        out.append(Sentencia(trad, orden, tipo, ins.group(1).lower() if ins else None))
    return tuple(out)


# ---------------------------------------------------------------- funciones de T-SQL que SQLite no tiene
def _txt(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _fecha(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _charindex(buscar, texto, desde=1):
    if buscar is None or texto is None:
        return None
    return _txt(texto).lower().find(_txt(buscar).lower(), max(int(desde or 1), 1) - 1) + 1


def _registrar_funciones(raw) -> None:
    det = {"deterministic": True}
    raw.create_function("LEFT", 2, lambda s, n: None if s is None or n is None else _txt(s)[:max(int(n), 0)], **det)
    raw.create_function("RIGHT", 2, lambda s, n: None if s is None or n is None
                        else (_txt(s)[-int(n):] if int(n) > 0 else ""), **det)
    raw.create_function("LEN", 1, lambda s: None if s is None else len(_txt(s).rstrip(" ")), **det)
    raw.create_function("CHARINDEX", 2, _charindex, **det)
    raw.create_function("CHARINDEX", 3, _charindex, **det)
    raw.create_function("CONCAT", -1, lambda *a: "".join("" if x is None else _txt(x) for x in a), **det)
    raw.create_function("YEAR", 1, lambda v: (_fecha(v) or None) and _fecha(v).year, **det)
    raw.create_function("MONTH", 1, lambda v: (_fecha(v) or None) and _fecha(v).month, **det)
    raw.create_function("DAY", 1, lambda v: (_fecha(v) or None) and _fecha(v).day, **det)


# ---------------------------------------------------------------- conexión (misma interfaz que database.Conexion)
def _corto(sql: str) -> str:
    return " ".join(str(sql).split())[:300]


def _filas(cur) -> list:
    if not cur.description:
        return []
    cols = [c[0] for c in cur.description]
    return [Row(cols, v) for v in cur.fetchall()]


@lru_cache(maxsize=1)
def _con_identidad() -> frozenset:
    """Tablas con id automático: solo en ellas el INSERT devuelve lastrowid (como SCOPE_IDENTITY)."""
    from . import esquema
    return frozenset(n for n, d in esquema.TABLAS.items() if "IDENTITY" in d.upper())


class ConexionSQLite:
    def __init__(self, raw, ruta: Path):
        self._raw = raw
        self.ruta = ruta

    # -- interno: SQL ya escrito para SQLite
    def _empezar(self) -> None:
        if not self._raw.in_transaction:
            self._raw.execute("BEGIN IMMEDIATE")      # la escritura espera su turno (hasta 30 s), como en SQL Server

    def _correr(self, sql: str, params=(), escritura: bool | None = None, muchos: bool = False):
        if escritura is None:
            escritura = bool(_ESCRITURA.match(sql))
        if escritura:
            self._empezar()
        try:
            return (self._raw.executemany if muchos else self._raw.execute)(sql, params)
        except sqlite3.IntegrityError as exc:
            raise pymssql.IntegrityError(f"{exc}  [SQLite: {_corto(sql)}]") from exc
        except sqlite3.Error as exc:
            raise type(exc)(f"{exc}  [SQLite: {_corto(sql)}]") from exc

    # -- interfaz de application.puertos.Conexion
    def execute(self, sql: str, params=()) -> Resultado:
        valores = [_valor(p) for p in params]
        primero, lastrowid = None, None
        es_insert = sql.lstrip()[:6].upper() == "INSERT"
        for s in traducir(sql):
            if s.tipo == "nada":
                continue
            if s.tipo == "bloqueo":
                self._empezar()
                filas = [Row(["r"], (0,))]
            else:
                cur = self._correr(s.sql, [valores[i] for i in s.orden], s.tipo == "escritura")
                filas = _filas(cur)
                if s.tabla_insert and lastrowid is None and s.tabla_insert in _con_identidad():
                    lastrowid = cur.lastrowid
            if primero is None and (filas or s.tipo != "escritura"):
                primero = filas
        if es_insert:
            return Resultado([], lastrowid)
        return Resultado(primero or [])

    def defer(self, sql: str, params=()) -> None:
        self.execute(sql, params)

    def executemany(self, sql: str, seq) -> None:
        sentencias = traducir(sql)
        if len(sentencias) == 1 and sentencias[0].tipo == "escritura":
            s = sentencias[0]
            lote = [[_valor(p[i]) for i in s.orden] for p in seq]
            if lote:
                self._correr(s.sql, lote, True, muchos=True)
            return
        for p in seq:
            self.execute(sql, p)

    def flush(self) -> None:
        pass

    def select_many(self, consultas) -> list:
        return [self.execute(s, p).fetchall() for s, p in consultas]

    def upsert(self, table: str, row: dict, keys: tuple, defer: bool = False) -> None:
        cols = list(row)
        resto = [c for c in cols if c not in keys]
        accion = ("UPDATE SET " + ", ".join(f"[{c}]=excluded.[{c}]" for c in resto)) if resto else "NOTHING"
        sql = (f"INSERT INTO [{table}]({', '.join(f'[{c}]' for c in cols)}) VALUES({', '.join('?' * len(cols))}) "
               f"ON CONFLICT({', '.join(f'[{k}]' for k in keys)}) DO {accion}")
        self._correr(sql, [_valor(row[c]) for c in cols], True)

    def commit(self) -> None:
        if self._raw.in_transaction:
            self._correr("COMMIT", (), False)

    def rollback(self) -> None:
        if self._raw.in_transaction:
            self._raw.execute("ROLLBACK")

    def close(self) -> None:
        try:
            self.rollback()
            self._raw.close()
        except sqlite3.ProgrammingError:          # ya estaba cerrada
            pass


def connect(ruta=None) -> ConexionSQLite:
    ruta = validar_ruta(ruta or ruta_base())
    ruta.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(ruta), timeout=ESPERA_BLOQUEO_S, isolation_level=None, check_same_thread=False)
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA synchronous = NORMAL")
    _registrar_funciones(raw)
    return ConexionSQLite(raw, ruta)


# ---------------------------------------------------------------- esquema (a partir de esquema.py)
_TIPO = re.compile(r"""(?P<identidad>\b(?:BIG|SMALL|TINY)?INT\s+IDENTITY\s*\(\s*1\s*,\s*1\s*\)\s+PRIMARY\s+KEY\b)
                     |(?P<texto>\b(?:N?VARCHAR|N?CHAR|N?TEXT)\b)(?:\s*\(\s*(?P<largo>MAX|\d+)\s*\))?
                     |(?P<bit>\bBIT\b)
                     |(?P<fecha>\b(?:DATETIME2|DATETIME|SMALLDATETIME|DATETIMEOFFSET|DATE|TIME|UNIQUEIDENTIFIER)\b
                        (?:\s*\(\s*\d+\s*\))?)""", re.I | re.X)


def _tipo(m) -> str:
    if m.group("identidad"):
        return "INTEGER PRIMARY KEY AUTOINCREMENT"
    if m.group("texto"):                                   # mismo nombre de tipo, sin distinguir mayúsculas
        largo = m.group("largo")
        nombre = m.group("texto").upper() + (f"({largo})" if largo and largo.upper() != "MAX" else "")
        return f"{nombre} COLLATE NOCASE"
    if m.group("bit"):
        return "INT"
    return "TEXT"


_RESTRICCION = ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN")


def _partes(codigo: str) -> list:
    """Divide la lista de columnas en las comas de primer nivel (no las de CHECK(...) ni PRIMARY KEY(...))."""
    out, nivel, desde = [], 0, 0
    for i, ch in enumerate(codigo):
        nivel += ch == "("
        nivel -= ch == ")"
        if ch == "," and nivel == 0:
            out.append(codigo[desde:i])
            desde = i + 1
    out.append(codigo[desde:])
    return [p.strip() for p in out if p.strip()]


def tipo_sqlite(tipo: str) -> str:
    """Un tipo de SQL Server (con sus restricciones) escrito para SQLite, p. ej. NVARCHAR(MAX) -> NVARCHAR COLLATE NOCASE."""
    codigo, lits = _enmascarar(tipo)
    codigo = _TIPO.sub(_tipo, codigo)
    return " ".join(_MARCA.sub(lambda m: lits[int(m.group(1))][1], codigo).split())


def columnas_sqlite(definicion: str) -> str:
    """Columnas de esquema.TABLAS (T-SQL) escritas para SQLite. El nombre de cada columna y los textos de CHECK y
    DEFAULT no se tocan (la tabla comments tiene una columna llamada text)."""
    codigo, lits = _enmascarar(definicion.replace("[{s}].", ""))
    out = []
    for parte in _partes(codigo):
        nombre, resto = (re.split(r"\s+", parte, maxsplit=1) + [""])[:2]
        if nombre.upper() in _RESTRICCION:
            out.append(parte)
        else:
            out.append(f"{nombre} {_TIPO.sub(_tipo, resto.strip())}")
    return " ".join(_MARCA.sub(lambda m: lits[int(m.group(1))][1], ", ".join(out)).split())


def _ajustes_al_copiar(tabla: str, cols: list) -> dict:
    """Valores que cambian al rehacer una tabla. Hoy: roles de versiones anteriores (si el catálogo los define)."""
    from ..domain import catalogos
    if tabla != "users" or "role" not in cols:
        return {}
    renombrados = getattr(catalogos, "ROLES_RENOMBRADOS", {})
    desactivados = getattr(catalogos, "ROLES_DESACTIVADOS", ())
    out = {}
    if renombrados:
        casos = " ".join(f"WHEN '{a}' THEN '{b}'" for a, b in renombrados.items())
        out["role"] = f"CASE [role] {casos} ELSE [role] END"
    if desactivados and "active" in cols:
        en = ", ".join(f"'{r}'" for r in desactivados)
        out["active"] = f"CASE WHEN [role] IN ({en}) THEN 0 ELSE [active] END"
    return out


def _columnas_de(raw, tabla: str) -> list:
    return [r[1] for r in raw.execute(f"PRAGMA table_info([{tabla}])")]


def _rehacer(raw, tablas: list, cuerpos: dict) -> None:
    """Ajusta tablas que cambiaron de definición (otra rama, otra versión) conservando sus datos."""
    raw.execute("PRAGMA foreign_keys = OFF")              # solo se puede cambiar fuera de una transacción
    try:
        raw.execute("BEGIN IMMEDIATE")
        for t in tablas:
            nueva = f"__nueva_{t}"
            raw.execute(f"DROP TABLE IF EXISTS [{nueva}]")
            raw.execute(f"CREATE TABLE [{nueva}]({cuerpos[t]})")
            viejas = {c.lower() for c in _columnas_de(raw, t)}
            comunes = [c for c in _columnas_de(raw, nueva) if c.lower() in viejas]
            ajustes = _ajustes_al_copiar(t, comunes)
            destino = ", ".join(f"[{c}]" for c in comunes)
            origen = ", ".join(ajustes.get(c, f"[{c}]") for c in comunes)
            raw.execute(f"INSERT INTO [{nueva}]({destino}) SELECT {origen} FROM [{t}]")
            raw.execute(f"DROP TABLE [{t}]")
            raw.execute(f"ALTER TABLE [{nueva}] RENAME TO [{t}]")
            raw.execute(f"INSERT OR REPLACE INTO [{_META}](tabla, ddl) VALUES(?, ?)", (t, cuerpos[t]))
        malas = raw.execute("PRAGMA foreign_key_check").fetchall()
        if malas:
            raise RuntimeError(f"llaves foráneas rotas: {malas[:5]}")
        raw.execute("COMMIT")
    except Exception as exc:
        if raw.in_transaction:
            raw.execute("ROLLBACK")
        raise RuntimeError(
            f"La base de pruebas no se pudo ajustar al esquema de esta rama (tablas: {', '.join(tablas)}): {exc}. "
            "Guárdela aparte y empiece una nueva con: .\\.venv\\Scripts\\python -m backlog_asp.cli.pruebas reiniciar") from exc
    finally:
        raw.execute("PRAGMA foreign_keys = ON")


def init_db(conn) -> None:
    """Crea o ajusta la base de pruebas con las mismas definiciones de esquema.py. Nunca borra datos."""
    from . import esquema
    from ..domain import catalogos
    if not isinstance(conn, ConexionSQLite):
        raise RuntimeError("init_db de SQLite recibió una conexión que no es de la base de pruebas.")
    conn.commit()                                          # igual que en SQL Server: confirma lo pendiente
    raw = conn._raw
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute(f"CREATE TABLE IF NOT EXISTS [{_META}](tabla TEXT PRIMARY KEY, ddl TEXT NOT NULL)")
    cuerpos = {t: columnas_sqlite(esquema.TABLAS[t]) for t in esquema.ORDEN}
    existentes = {r[0].lower() for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    guardadas = dict(raw.execute(f"SELECT tabla, ddl FROM [{_META}]").fetchall())
    cambiaron = [t for t in esquema.ORDEN if t.lower() in existentes and guardadas.get(t) != cuerpos[t]]
    if cambiaron:
        _rehacer(raw, cambiaron, cuerpos)

    conn._empezar()
    for t in esquema.ORDEN:
        if t.lower() not in existentes:
            raw.execute(f"CREATE TABLE [{t}]({cuerpos[t]})")
            raw.execute(f"INSERT OR REPLACE INTO [{_META}](tabla, ddl) VALUES(?, ?)", (t, cuerpos[t]))
    for idx, tabla, cols in esquema.INDICES:
        raw.execute(f"CREATE INDEX IF NOT EXISTS [{idx}] ON [{tabla}]({cols})")
    for tabla, columna, tipo in esquema.MIGRACIONES:
        if columna.lower() not in {c.lower() for c in _columnas_de(raw, tabla)}:
            raw.execute(f"ALTER TABLE [{tabla}] ADD COLUMN [{columna}] {tipo_sqlite(tipo)}")
    for tabla, msg in esquema.SOLO_LECTURA:                # en SQL Server: triggers INSTEAD OF con THROW
        texto = msg.replace("'", "''")
        for accion in ("UPDATE", "DELETE"):
            nombre = f"{tabla}_solo_lectura_{accion.lower()}"
            raw.execute(f"DROP TRIGGER IF EXISTS [{nombre}]")
            raw.execute(f"CREATE TRIGGER [{nombre}] BEFORE {accion} ON [{tabla}] BEGIN SELECT RAISE(ABORT, '{texto}'); END")
    for antes, ahora in getattr(catalogos, "ESTADOS_RENOMBRADOS", {}).items():
        raw.execute("UPDATE cases SET estado=? WHERE estado=?", (ahora, antes))
    for k, v in catalogos.DEFAULT_SETTINGS.items():
        raw.execute("INSERT OR IGNORE INTO settings([key], value) VALUES(?, ?)", (k, v))
    conn.commit()


def drop_all(conn) -> None:
    """Borra la base de una prueba automática. Se niega con cualquier base que no sea de pruebas (backlog_test*)."""
    s = config.DB_SCHEMA
    if not s.startswith("backlog_test"):
        raise RuntimeError(f"drop_all solo se permite en esquemas de prueba (backlog_test*), no en '{s}'.")
    ruta = Path(conn.ruta)
    if not _dentro(ruta, Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError(f"drop_all solo borra bases de prueba de la carpeta temporal, no {ruta}.")
    conn.close()
    for sufijo in ("", "-wal", "-shm"):
        try:
            os.remove(f"{ruta}{sufijo}")
        except OSError:                                    # no existe, o Windows la tiene abierta en otro hilo
            pass
