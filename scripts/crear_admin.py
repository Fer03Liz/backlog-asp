"""Crea un usuario administrador. Uso: python scripts/crear_admin.py"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, security  # noqa: E402


def main():
    db.init_db()
    if "--si-no-hay" in sys.argv:
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        if n:
            return
        print("Primera vez: cree el usuario administrador.")
    username = input("Usuario (ej. fer.planner): ").strip()
    full_name = input("Nombre completo (aparece en la firma de los correos): ").strip()
    while True:
        pw = getpass.getpass("Contraseña (mín. 12 caracteres, letras y números): ")
        problem = security.password_problem(pw, username)
        if problem:
            print(problem)
            continue
        if pw != getpass.getpass("Repita la contraseña: "):
            print("Las contraseñas no coinciden.")
            continue
        break
    conn = db.connect()
    try:
        security.create_user(conn, username, full_name, pw, "admin", must_change=False)
        db.audit(conn, None, "user_create", username, {"rol": "admin", "origen": "consola"})
        conn.commit()
        print(f"Administrador '{username}' creado.")
    except Exception as exc:
        print(f"No se pudo crear el usuario: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
