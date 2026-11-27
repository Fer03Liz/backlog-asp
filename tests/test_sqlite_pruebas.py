"""Modo pruebas (SQLite): traducción de T-SQL, rechazo de SQL que fallaría en SQL Server y protecciones.

No necesita SQL Server: usa bases SQLite temporales. Corre siempre con `poetry run pytest`.
"""
import pytest
import pymssql

from backlog_asp import config
from backlog_asp.infrastructure import database, esquema
from backlog_asp.infrastructure import sqlite_pruebas as sp
from backlog_asp.infrastructure.sqlite_pruebas import SQLNoPortable, traducir


def _una(sql):
    (s,) = traducir(sql)
    return s


# ---------------------------------------------------------------- traducción
@pytest.mark.parametrize("tsql, sqlite", [
    ("SELECT TOP 15 file_date FROM loads ORDER BY id DESC", "SELECT file_date FROM loads ORDER BY id DESC LIMIT 15"),
    ("SELECT DISTINCT TOP 2 file_date FROM x ORDER BY file_date DESC", "SELECT DISTINCT file_date FROM x ORDER BY file_date DESC LIMIT 2"),
    ("SELECT TOP 1 1 x FROM cases", "SELECT 1 x FROM cases LIMIT 1"),
    ("SELECT [key], value FROM settings", "SELECT [key], value FROM settings"),
    ("SELECT * FROM lotes WHERE LEFT(at, 10)=?", "SELECT * FROM lotes WHERE LEFT(at, 10)=?"),
    ("SELECT ISNULL(a, 0) FROM t", "SELECT ifnull(a, 0) FROM t"),
    ("SELECT a FROM t WITH (NOLOCK) WHERE b=N'ñandú'", "SELECT a FROM t WHERE b='ñandú'"),
    ("UPDATE t WITH (UPDLOCK, SERIALIZABLE) SET a=? WHERE b=?", "UPDATE t SET a=? WHERE b=?"),
    ("TRUNCATE TABLE kgb", "DELETE FROM kgb"),
    ("SELECT a FROM t ORDER BY a OFFSET 20 ROWS FETCH NEXT 10 ROWS ONLY", "SELECT a FROM t ORDER BY a LIMIT 10 OFFSET 20"),
    ("SELECT 'LIMIT || TRUE; x' AS texto", "SELECT 'LIMIT || TRUE; x' AS texto"),
])
def test_traduce(tsql, sqlite):
    assert " ".join(_una(tsql).sql.split()) == sqlite


def test_top_con_parametro_va_al_final():
    s = _una("SELECT TOP (?) at FROM audit_log WHERE target=? ORDER BY id DESC")
    assert s.sql.endswith("LIMIT ?") and s.orden == (1, 0)


def test_sentencias_sin_efecto_y_bloqueo_de_aplicacion():
    assert [s.tipo for s in traducir("SET NOCOUNT ON; SET IDENTITY_INSERT [backlog].[users] ON")] == ["nada", "nada"]
    lock = ("DECLARE @r INT; EXEC @r = sp_getapplock @Resource=N'caso_calculado', @LockMode=N'Exclusive', "
            "@LockOwner=N'Transaction', @LockTimeout=0; SELECT @r AS r")
    assert traducir(lock)[0].tipo == "bloqueo"


# ---------------------------------------------------------------- lo que fallaría en SQL Server
@pytest.mark.parametrize("sql, pista", [
    ("SELECT a FROM t LIMIT 5", "TOP"),
    ("SELECT a || b FROM t", "||"),
    ("INSERT OR REPLACE INTO t(a) VALUES(?)", "upsert"),
    ("INSERT INTO t(a) VALUES(?) ON CONFLICT(a) DO NOTHING", "upsert"),
    ("INSERT INTO t(a) VALUES(?) RETURNING id", "lastrowid"),
    ("SELECT a FROM t WHERE activo = TRUE", "1 y 0"),
    ("SELECT ifnull(a, 0) FROM t", "ifnull"),
    ("SELECT length(a) FROM t", "length"),
    ("SELECT substr(a, 1, 3) FROM t", "substr"),
    ("SELECT date('now')", "date"),
    ("SELECT \"a\" FROM t", "corchetes"),
    ("SELECT MAX(a, b) FROM t", "CASE"),
    ("SELECT 'Caso ' + key_notif FROM cases", "Python"),
    ("SELECT a FROM t WHERE a == 1", "="),
])
def test_rechaza_lo_que_falla_en_sql_server(sql, pista):
    with pytest.raises(SQLNoPortable, match=None) as exc:
        traducir(sql)
    assert pista in str(exc.value)


@pytest.mark.parametrize("sql", [
    "SELECT GETDATE()", "SELECT DATEADD(day, 1, eta) FROM t", "INSERT INTO t(a) OUTPUT INSERTED.id VALUES(1)",
    "MERGE t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET b = s.b;", "SELECT SCOPE_IDENTITY()",
    "SELECT a FROM t WHERE b IN (SELECT TOP 3 b FROM s)", "SELECT STRING_AGG(a, ',') FROM t",
    "SELECT name FROM sys.columns", "IF NOT EXISTS (SELECT 1 FROM t) INSERT INTO t(a) VALUES(1)",
])
def test_avisa_el_tsql_que_no_traduce(sql):
    with pytest.raises(SQLNoPortable, match="no traduce"):
        traducir(sql)


# ---------------------------------------------------------------- base completa
@pytest.fixture()
def conn(tmp_path):
    c = sp.connect(tmp_path / "prueba.db")
    sp.init_db(c)
    yield c
    c.close()


def _usuario(conn, username="ana", role="admin"):
    return conn.execute("INSERT INTO users(username, full_name, pw_hash, role, created_at) VALUES(?,?,?,?,?)",
                        (username, "Ana", "x", role, "2026-11-27T10:00:00")).lastrowid


def test_esquema_igual_al_de_sql_server(conn):
    tablas = {r[0] for r in conn._raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with pytest.raises(SQLNoPortable, match="sqlite_master"):     # por la interfaz de la app, se rechaza
        conn.execute("SELECT name FROM sqlite_master")
    assert set(esquema.ORDEN) <= tablas
    for t in esquema.ORDEN:
        assert conn.execute(f"SELECT TOP 1 * FROM {t}").fetchall() == [] or t == "settings"
    assert conn.execute("SELECT COUNT(*) n FROM settings").fetchone()["n"] > 0


def test_insert_upsert_y_lastrowid(conn):
    uid = _usuario(conn)
    assert uid == 1 and _usuario(conn, "luis") == 2
    assert conn.execute("INSERT INTO settings([key], value) VALUES(?, ?)", ("k", "v")).lastrowid is None
    conn.upsert("settings", {"key": "k", "value": "w"}, ("key",))
    conn.upsert("holidays_extra", {"day": "2026-12-08", "name": "Inmaculada"}, ("day",), defer=True)
    conn.commit()
    assert conn.execute("SELECT value FROM settings WHERE [key]='k'").fetchone()["value"] == "w"
    assert conn.select_many([("SELECT COUNT(*) n FROM users", ()), ("SELECT name FROM holidays_extra", ())])[1][0][0] == "Inmaculada"


def test_se_comporta_como_sql_server(conn):
    _usuario(conn, "Fer.Planner")
    conn.commit()
    with pytest.raises(pymssql.IntegrityError):              # sin distinguir mayúsculas, como la intercalación CI
        _usuario(conn, "fer.planner")
    conn.rollback()
    assert conn.execute("SELECT id FROM users WHERE username=?", ("FER.PLANNER",)).fetchone()
    with pytest.raises(pymssql.IntegrityError):              # llaves foráneas activas
        conn.execute("INSERT INTO sessions(token_hash, user_id, csrf, created_at, last_seen, expires_at) "
                     "VALUES('t', 999, 'c', 'a', 'b', 'c')")
    conn.rollback()
    with pytest.raises(pymssql.IntegrityError):              # CHECK de roles
        _usuario(conn, "otro", "superusuario")
    conn.rollback()
    conn.execute("INSERT INTO audit_log(at, action) VALUES('2026-11-27', 'x')")
    conn.execute("INSERT INTO comments(key_notif, at, text) VALUES('k', '2026-11-27', 'hola')")
    conn.commit()
    for tabla in ("audit_log", "comments"):                  # bitácora y comentarios de solo lectura
        with pytest.raises(pymssql.IntegrityError, match="lectura|borran"):
            conn.execute(f"DELETE FROM {tabla}")
        conn.rollback()
        with pytest.raises(pymssql.IntegrityError):
            conn.execute(f"UPDATE {tabla} SET at='x'")
        conn.rollback()
    assert conn.execute("SELECT LEFT(at, 4) a, LEN('ab  ') n FROM audit_log").fetchone()[0] == "2026"
    with pytest.raises(SQLNoPortable, match="NaN"):
        conn.execute("UPDATE kgb SET qty=?", (float("nan"),))


def test_ajusta_una_tabla_que_cambio_sin_perder_datos(conn, monkeypatch):
    _usuario(conn, "ana", "admin")
    conn.commit()
    nueva = esquema.TABLAS["users"].replace("CHECK(role IN (", "CHECK(role IN ('nuevo',") + ", apodo NVARCHAR(40)"
    assert nueva != esquema.TABLAS["users"] + ", apodo NVARCHAR(40)"
    monkeypatch.setitem(esquema.TABLAS, "users", nueva)
    sp.init_db(conn)
    assert conn.execute("SELECT role FROM users WHERE username='ana'").fetchone()["role"] == "admin"
    _usuario(conn, "beto", "nuevo")
    conn.execute("UPDATE users SET apodo='B' WHERE username='beto'")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 2


# ---------------------------------------------------------------- protecciones
def test_nunca_abre_la_base_anterior_ni_los_respaldos():
    data = config.PROJECT_DIR / "data"
    for ruta in (data / "backlog.db", data / "backups" / "backlog_20261001.db"):
        with pytest.raises(RuntimeError, match="no la abre"):
            sp.validar_ruta(ruta)
    assert sp.validar_ruta(data / "pruebas" / "backlog_pruebas.db")


def test_en_modo_pruebas_no_se_conecta_a_sql_server(monkeypatch):
    monkeypatch.setattr(config, "DB_ENGINE", "sqlite")
    with pytest.raises(RuntimeError, match="no se conecta a SQL Server"):
        database.engine()


def test_drop_all_solo_borra_bases_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_SCHEMA", "backlog")
    c = sp.connect(tmp_path / "otra.db")
    with pytest.raises(RuntimeError, match="solo se permite"):
        sp.drop_all(c)
    c.close()
    assert (tmp_path / "otra.db").exists()
