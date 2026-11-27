"""Base SQLite de pruebas (ver PRUEBAS.md). Este comando siempre trabaja en modo pruebas: nunca toca SQL Server.

Uso:
  .\\.venv\\Scripts\\python -m backlog_asp.cli.pruebas                  estado de la base de pruebas
  .\\.venv\\Scripts\\python -m backlog_asp.cli.pruebas copiar RUTA.db   copia los datos de una base SQLite
                                                                      anterior (la abre en solo lectura)
  .\\.venv\\Scripts\\python -m backlog_asp.cli.pruebas reiniciar        guarda la base actual como respaldo;
                                                                      la próxima vez el tablero empieza vacío
"""
import os

os.environ["BACKLOG_DB_ENGINE"] = "sqlite"      # antes de leer la configuración: aquí nunca se usa SQL Server

import shutil  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from .. import config  # noqa: E402
from ..domain import catalogos  # noqa: E402
from ..infrastructure import database, esquema, sqlite_pruebas  # noqa: E402


def _ruta() -> Path:
    if config.DB_ENGINE != "sqlite":
        sys.exit("La configuración ya estaba cargada en modo SQL Server: ejecute este comando solo.")
    return sqlite_pruebas.validar_ruta(sqlite_pruebas.ruta_base())


def estado() -> None:
    ruta = _ruta()
    print(f"Base de pruebas: {ruta}")
    if not ruta.exists():
        print("Todavía no existe: se crea sola al abrir el tablero con iniciar_pruebas.bat.")
        return
    print(f"Tamaño: {ruta.stat().st_size / 1024 / 1024:.1f} MB")
    con = sqlite3.connect(ruta.as_uri() + "?mode=ro", uri=True)
    try:
        hay = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in esquema.ORDEN:
            if t in hay:
                n = con.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                if n:
                    print(f"  {t:<20}{n:>9,}")
        if "users" in hay:
            print("Usuarios:")
            for u, rol, activo in con.execute("SELECT username, role, active FROM users ORDER BY username"):
                print(f"  {u:<24}{rol:<12}{'' if activo else '(inactivo)'}")
    finally:
        con.close()


def _ajustar_roles(cols: list, filas: list) -> list:
    """Roles de versiones anteriores -> roles actuales, si el catálogo de esta rama los define."""
    renombrados = getattr(catalogos, "ROLES_RENOMBRADOS", {})
    desactivados = getattr(catalogos, "ROLES_DESACTIVADOS", ())
    if "role" not in cols or not (renombrados or desactivados):
        return filas
    i, a = cols.index("role"), cols.index("active") if "active" in cols else None
    out = []
    for f in filas:
        f = list(f)
        if a is not None and f[i] in desactivados:
            f[a] = 0
        f[i] = renombrados.get(f[i], f[i])
        out.append(tuple(f))
    return out


def copiar(origen: str) -> None:
    destino = _ruta()
    fuente = Path(origen).resolve()
    if not fuente.is_file():
        sys.exit(f"No existe el archivo {fuente}.")
    if os.path.normcase(str(fuente)) == os.path.normcase(str(destino)):
        sys.exit("El origen es la misma base de pruebas.")
    # immutable=1: SQLite la lee sin bloquearla ni crear archivos -wal/-shm a su lado
    src = sqlite3.connect(fuente.as_uri() + "?mode=ro&immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    conn = database.connect()
    try:
        origen_tablas = {r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        esquema.init_db(conn)
        ocupadas = [t for t in esquema.ORDEN if t != "settings" and conn.execute(f"SELECT TOP 1 1 x FROM {t}").fetchone()]
        if ocupadas:
            sys.exit(f"La base de pruebas ya tiene datos ({', '.join(ocupadas)}). No se copia nada.\n"
                     "Para empezar de cero: .\\.venv\\Scripts\\python -m backlog_asp.cli.pruebas reiniciar")
        print(f"Copiando {fuente} (solo lectura) a {destino}")
        for t in esquema.ORDEN:
            if t not in origen_tablas or t == "sessions":          # las sesiones viejas no sirven
                continue
            propias = {c.lower() for c in sqlite_pruebas._columnas_de(conn._raw, t)}
            cols = [r["name"] for r in src.execute(f"PRAGMA table_info([{t}])") if r["name"].lower() in propias]
            filas = [tuple(r) for r in src.execute(f"SELECT {', '.join(f'[{c}]' for c in cols)} FROM [{t}]")]
            if t == "users":
                filas = _ajustar_roles(cols, filas)
            if t == "settings":
                conn.execute("DELETE FROM settings")
            conn.executemany(f"INSERT INTO {t}({', '.join(f'[{c}]' for c in cols)}) VALUES({', '.join('?' * len(cols))})",
                             filas)
            print(f"  {t:<20}{len(filas):>9,}")
        conn.commit()
        esquema.init_db(conn)              # parámetros nuevos que falten y estados con nombre nuevo
        print("Copia terminada. Abra el tablero con iniciar_pruebas.bat (los usuarios y claves son los de esa base).")
    except Exception as exc:
        conn.rollback()
        sys.exit(f"No se copió nada: {exc}")
    finally:
        conn.close()
        src.close()


def reiniciar(confirmado: bool = False) -> None:
    ruta = _ruta()
    if not ruta.exists():
        print("No hay base de pruebas: no hay nada que reiniciar.")
        return
    if not confirmado:
        r = input(f"Se guardará {ruta.name} como respaldo y la próxima vez el tablero empezará con una base vacía.\n"
                  "Escriba SI para continuar: ")
        if r.strip().upper() != "SI":
            print("Cancelado.")
            return
    try:
        con = sqlite3.connect(str(ruta))
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")     # todo queda en el .db antes de moverlo
        con.close()
        carpeta = ruta.parent / "respaldos"
        carpeta.mkdir(exist_ok=True)
        destino = carpeta / f"{ruta.stem}_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.move(str(ruta), str(destino))
        for sufijo in ("-wal", "-shm"):
            Path(f"{ruta}{sufijo}").unlink(missing_ok=True)
    except (PermissionError, sqlite3.OperationalError) as exc:
        sys.exit(f"No se pudo mover la base ({exc}). Cierre el tablero de pruebas y vuelva a intentarlo.")
    print(f"Respaldo guardado en {destino}")


def main(argv=None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] == "estado":
        estado()
    elif args[0] == "copiar" and len(args) == 2:
        copiar(args[1])
    elif args[0] == "reiniciar":
        reiniciar("--si" in args)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
