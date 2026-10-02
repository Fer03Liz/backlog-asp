"""Contraseñas, sesiones y bloqueo de cuentas.

- Contraseñas con scrypt (librería estándar), sal aleatoria y comparación en tiempo constante.
- La cookie solo lleva un token aleatorio; en la base se guarda su hash SHA-256.
- Cada sesión tiene un token CSRF que el navegador debe enviar en toda escritura.
"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from . import config
from .db import now_iso

COOKIE_NAME = "bsid"
_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 64 * 1024 * 1024
_dummy_hash = None


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                              p=int(p), maxmem=_MAXMEM, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def burn_time(password: str) -> None:
    """Iguala el tiempo de respuesta cuando el usuario no existe."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-000")
    verify_password(password, _dummy_hash)


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < config.MIN_PASSWORD_LEN:
        return f"La contraseña debe tener al menos {config.MIN_PASSWORD_LEN} caracteres."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "La contraseña debe combinar letras y números."
    if username and username.lower() in password.lower():
        return "La contraseña no puede contener el nombre de usuario."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id: int) -> str:
    now = datetime.now()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, csrf, created_at, last_seen, expires_at) VALUES(?,?,?,?,?,?)",
        (_token_hash(token), user_id, secrets.token_urlsafe(24), now_iso(), now_iso(),
         (now + timedelta(hours=config.SESSION_HOURS)).isoformat(timespec="seconds")),
    )
    return token


def load_session(conn, token: str):
    row = conn.execute(
        """SELECT s.token_hash, s.csrf, s.last_seen, s.expires_at, u.id, u.username, u.full_name, u.role,
                  u.active, u.must_change_pw
           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
        (_token_hash(token),),
    ).fetchone()
    if not row:
        return None
    now = datetime.now()
    idle_limit = datetime.fromisoformat(row["last_seen"]) + timedelta(minutes=config.IDLE_MINUTES)
    if not row["active"] or now > datetime.fromisoformat(row["expires_at"]) or now > idle_limit:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        conn.commit()
        return None
    conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now_iso(), row["token_hash"]))
    conn.commit()
    return dict(row)


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def delete_user_sessions(conn, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def is_locked(user_row) -> bool:
    lu = user_row["locked_until"]
    return bool(lu) and datetime.now() < datetime.fromisoformat(lu)


def register_failed_login(conn, user_row) -> None:
    attempts = user_row["failed_attempts"] + 1
    locked = None
    if attempts >= config.MAX_FAILED_LOGINS:
        locked = (datetime.now() + timedelta(minutes=config.LOCK_MINUTES)).isoformat(timespec="seconds")
        attempts = 0
    conn.execute("UPDATE users SET failed_attempts = ?, locked_until = COALESCE(?, locked_until) WHERE id = ?",
                 (attempts, locked, user_row["id"]))


def register_successful_login(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user_id,))


def create_user(conn, username: str, full_name: str, password: str, role: str, must_change: bool) -> int:
    if role not in config.ROLES:
        raise ValueError("Rol no válido.")
    if not username or not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise ValueError("El usuario solo puede tener letras, números, punto, guion y guion bajo.")
    problem = password_problem(password, username)
    if problem:
        raise ValueError(problem)
    cur = conn.execute(
        "INSERT INTO users(username, full_name, pw_hash, role, must_change_pw, created_at) VALUES(?,?,?,?,?,?)",
        (username.strip(), full_name.strip(), hash_password(password), role, int(must_change), now_iso()),
    )
    return cur.lastrowid
