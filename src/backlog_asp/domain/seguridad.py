"""Política de contraseñas y hash.

- Contraseñas con scrypt (librería estándar), sal aleatoria y comparación en tiempo constante.
- La cookie solo lleva un token aleatorio; en la base se guarda su hash SHA-256.
"""
import base64
import hashlib
import hmac
import secrets

from .catalogos import ROLES

MIN_PASSWORD_LEN = 12
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
    if len(password) < MIN_PASSWORD_LEN:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "La contraseña debe combinar letras y números."
    if username and username.lower() in password.lower():
        return "La contraseña no puede contener el nombre de usuario."
    return None


def user_problem(username: str, role: str) -> str | None:
    if role not in ROLES:
        return "Rol no válido."
    if not username or not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        return "El usuario solo puede tener letras, números, punto, guion y guion bajo."
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
