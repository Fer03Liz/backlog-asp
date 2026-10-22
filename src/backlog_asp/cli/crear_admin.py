import getpass
import sys

from ..application import sesiones
from ..application.auditoria import audit
from ..domain import seguridad
from ..domain.errores import ErrorNegocio
from ..infrastructure import database, esquema


def main():
    conn = database.connect()
    try:
        esquema.init_db(conn)
        if "--si-no-hay" in sys.argv:
            if conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]:
                return
            print("Primera vez: cree el usuario administrador.")
        username = input("Usuario (ej. fer.planner): ").strip()
        full_name = input("Nombre completo (aparece en la firma de los correos): ").strip()
        while True:
            pw = getpass.getpass("Contraseña (mín. 12 caracteres, letras y números): ")
            problem = seguridad.password_problem(pw, username)
            if problem:
                print(problem)
                continue
            if pw != getpass.getpass("Repita la contraseña: "):
                print("Las contraseñas no coinciden.")
                continue
            break
        try:
            sesiones.create_user(conn, username, full_name, pw, "admin", must_change=False)
            audit(conn, None, "user_create", username, {"rol": "admin", "origen": "consola"})
            conn.commit()
            print(f"Administrador '{username}' creado.")
        except ErrorNegocio as exc:
            print(f"No se pudo crear el usuario: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
