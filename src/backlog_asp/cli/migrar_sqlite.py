"""Copia los datos de la base SQLite de la versión anterior (data/backlog.db) a SQL Server.

Uso: poetry run backlog-migrar-sqlite RUTA\\backlog.db

- Crea el esquema si no existe y copia tabla por tabla, conservando los id (bitácora, comentarios, lotes...).
- Se niega si alguna tabla de destino ya tiene datos (salvo los parámetros por defecto): no mezcla bases.
- Todo va en una transacción: si algo falla, SQL Server queda como estaba.
"""
import sqlite3
import sys

from .. import config
from ..infrastructure import database, esquema

IDENTIDAD = {"users", "loads", "case_history", "comments", "audit_log", "emails_log", "miami_purchases",
             "solicitudes_fecha", "lotes"}


def _columnas_destino(conn, table) -> list:
    return [r["name"] for r in conn.execute(
        "SELECT name FROM sys.columns WHERE object_id = OBJECT_ID(?) ORDER BY column_id", (f"[{config.DB_SCHEMA}].[{table}]",))]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    origen = {r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn = database.connect()
    try:
        esquema.init_db(conn)
        ocupadas = [t for t in esquema.ORDEN if t != "settings" and t in origen
                    and conn.execute(f"SELECT TOP 1 1 x FROM {t}").fetchone()]
        if ocupadas:
            print(f"El esquema '{config.DB_SCHEMA}' ya tiene datos en: {', '.join(ocupadas)}. No se copia nada.")
            sys.exit(1)
        for table in esquema.ORDEN:
            if table not in origen:
                continue
            destino = _columnas_destino(conn, table)
            cols = [c for c in (r[1] for r in src.execute(f"PRAGMA table_info({table})")) if c in destino]
            filas = [tuple(r[c] for c in cols) for r in src.execute(f"SELECT {', '.join(cols)} FROM {table}")]
            if table == "settings":
                conn.execute("DELETE FROM settings")
            identidad = table in IDENTIDAD and "id" in cols
            if identidad:
                conn.execute(f"SET IDENTITY_INSERT [{config.DB_SCHEMA}].[{table}] ON")
            conn.executemany(f"INSERT INTO {table}({', '.join(f'[{c}]' for c in cols)}) VALUES({', '.join('?' * len(cols))})", filas)
            if identidad:
                conn.execute(f"SET IDENTITY_INSERT [{config.DB_SCHEMA}].[{table}] OFF")
            print(f"{table}: {len(filas)} filas")
        conn.commit()
        print("Migración terminada.")
    finally:
        conn.close()
        src.close()


if __name__ == "__main__":
    main()
