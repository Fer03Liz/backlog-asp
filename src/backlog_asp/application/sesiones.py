"""Ingreso, sesiones, bloqueo por intentos fallidos y administración de usuarios.

Cada sesión tiene un token CSRF que el navegador debe enviar en toda escritura.
"""
from datetime import datetime, timedelta

from .. import config
from ..domain import seguridad
from ..domain.catalogos import ROLES
from ..domain.errores import ErrorNegocio
from .auditoria import audit, now_iso

GENERICO = "Usuario o contraseña incorrectos."
_TOQUE_SEGUNDOS = 60       # last_seen se actualiza como máximo una vez por minuto (ahorra un viaje a la base)


def _ts(x: datetime) -> str:
    return x.isoformat(timespec="seconds")


def create_session(conn, user_id: int) -> str:
    now = datetime.now()
    conn.defer("DELETE FROM sessions WHERE expires_at < ?", (_ts(now),))
    token = seguridad.new_token()
    conn.defer(
        "INSERT INTO sessions(token_hash, user_id, csrf, created_at, last_seen, expires_at) VALUES(?,?,?,?,?,?)",
        (seguridad.token_hash(token), user_id, seguridad.new_csrf(), _ts(now), _ts(now),
         _ts(now + timedelta(hours=config.SESSION_HOURS))))
    return token


def load_session(conn, token: str):
    row = conn.execute(
        """SELECT s.token_hash, s.csrf, s.last_seen, s.expires_at, u.id, u.username, u.full_name, u.role,
                  u.active, u.must_change_pw
           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
        (seguridad.token_hash(token),),
    ).fetchone()
    if not row:
        return None
    now = datetime.now()
    last_seen = datetime.fromisoformat(row["last_seen"])
    if not row["active"] or now > datetime.fromisoformat(row["expires_at"]) or now > last_seen + timedelta(minutes=config.IDLE_MINUTES):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        conn.commit()
        return None
    if (now - last_seen).total_seconds() >= _TOQUE_SEGUNDOS:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (_ts(now), row["token_hash"]))
        conn.commit()
    return dict(row)


def delete_session(conn, token: str) -> None:
    conn.defer("DELETE FROM sessions WHERE token_hash = ?", (seguridad.token_hash(token),))


def delete_user_sessions(conn, user_id: int) -> None:
    conn.defer("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def _is_locked(user_row) -> bool:
    lu = user_row["locked_until"]
    return bool(lu) and datetime.now() < datetime.fromisoformat(lu)


def login(conn, username: str, password: str):
    """Devuelve (token, usuario). Los errores van a la bitácora y se confirman aunque el ingreso falle."""
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if not user or not user["active"]:
        seguridad.burn_time(password)
        audit(conn, None, "login_failed", username[:64])
        conn.commit()
        raise ErrorNegocio(GENERICO, 401)
    user = dict(user)
    if _is_locked(user):
        audit(conn, user, "login_locked", user["username"])
        conn.commit()
        raise ErrorNegocio("Usuario bloqueado temporalmente por intentos fallidos. Intente más tarde.", 423)
    if not seguridad.verify_password(password, user["pw_hash"]):
        attempts, locked = user["failed_attempts"] + 1, None
        if attempts >= config.MAX_FAILED_LOGINS:
            locked, attempts = _ts(datetime.now() + timedelta(minutes=config.LOCK_MINUTES)), 0
        conn.defer("UPDATE users SET failed_attempts = ?, locked_until = COALESCE(?, locked_until) WHERE id = ?",
                   (attempts, locked, user["id"]))
        audit(conn, user, "login_failed", user["username"])
        conn.commit()
        raise ErrorNegocio(GENERICO, 401)
    conn.defer("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user["id"],))
    token = create_session(conn, user["id"])
    audit(conn, user, "login", user["username"])
    conn.commit()
    return token, user


def change_password(conn, user, current: str, new: str) -> str:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not seguridad.verify_password(current, row["pw_hash"]):
        raise ErrorNegocio("La contraseña actual no es correcta.", 400)
    problem = seguridad.password_problem(new, row["username"])
    if problem:
        raise ErrorNegocio(problem, 400)
    if new == current:
        raise ErrorNegocio("La nueva contraseña debe ser distinta.", 400)
    conn.defer("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (seguridad.hash_password(new), user["id"]))
    delete_user_sessions(conn, user["id"])
    token = create_session(conn, user["id"])
    audit(conn, user, "password_change", user["username"])
    return token


# ---------------------------------------------------------------- usuarios
def create_user(conn, username: str, full_name: str, password: str, role: str, must_change: bool) -> int:
    problem = seguridad.user_problem(username, role) or seguridad.password_problem(password, username)
    if problem:
        raise ErrorNegocio(problem, 422)
    if conn.execute("SELECT 1 x FROM users WHERE username=?", (username.strip(),)).fetchone():
        raise ErrorNegocio("Ese usuario ya existe.", 409)
    cur = conn.execute(
        "INSERT INTO users(username, full_name, pw_hash, role, must_change_pw, created_at) VALUES(?,?,?,?,?,?)",
        (username.strip(), (full_name or "").strip(), seguridad.hash_password(password), role, int(must_change), now_iso()),
    )
    return cur.lastrowid


def list_users(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT id, username, full_name, role, active, must_change_pw, locked_until, created_at FROM users ORDER BY username")]


def update_user(conn, admin, uid: int, role=None, active=None, new_password=None, unlock=None) -> None:
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise ErrorNegocio("Usuario no encontrado.", 404)
    if uid == admin["id"] and (active is False or (role and role != "admin")):
        raise ErrorNegocio("No puede desactivarse ni quitarse el rol de administrador a sí mismo.", 400)
    detail = {}
    if role:
        if role not in ROLES:
            raise ErrorNegocio("Rol no válido.", 422)
        conn.defer("UPDATE users SET role=? WHERE id=?", (role, uid))
        detail["rol"] = role
    if active is not None:
        conn.defer("UPDATE users SET active=? WHERE id=?", (int(active), uid))
        detail["activo"] = active
        if not active:
            delete_user_sessions(conn, uid)
    if new_password:
        problem = seguridad.password_problem(new_password, row["username"])
        if problem:
            raise ErrorNegocio(problem, 422)
        conn.defer("UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0, locked_until=NULL WHERE id=?",
                   (seguridad.hash_password(new_password), uid))
        delete_user_sessions(conn, uid)
        detail["clave_restablecida"] = True
    if unlock:
        conn.defer("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
        detail["desbloqueado"] = True
    audit(conn, admin, "user_update", row["username"], detail)
