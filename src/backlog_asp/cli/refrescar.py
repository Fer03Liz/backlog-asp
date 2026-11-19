"""Reconstruye la tabla caso_calculado (la que lee Power BI).

Uso: poetry run backlog-refrescar
Pensado para una tarea programada que corra justo antes de la actualización de Power BI.
"""
from ..application import calculado
from ..infrastructure import database, esquema


def main():
    conn = database.connect()
    try:
        esquema.init_db(conn)
        n = calculado.refrescar(conn)
    finally:
        conn.close()
    print("Otro proceso ya la está actualizando." if n < 0 else f"caso_calculado actualizada: {n} casos.")


if __name__ == "__main__":
    main()
