"""Dependencias comunes: conexión por petición, usuario de la sesión, CSRF y permisos por rol."""
import hmac
from datetime import date

from fastapi import Depends, HTTPException, Request

from ...application import acceso, sesiones
from ...infrastructure import database

COOKIE_NAME = "bsid"
ALLOWED_WITH_PENDING_PW = {"/api/me", "/api/password", "/api/logout"}


def get_db():
    conn = database.connect()
    try:
        yield conn
    finally:
        conn.close()            # lo que no se confirmó se deshace al volver al pool


def current_user(request: Request, conn=Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    session = sesiones.load_session(conn, token) if token else None
    if not session:
        raise HTTPException(401, "Sesión no válida o expirada.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, session["csrf"]):
            raise HTTPException(403, "Solicitud rechazada (token CSRF).")
    if session["must_change_pw"] and request.url.path not in ALLOWED_WITH_PENDING_PW:
        raise HTTPException(403, "Debe cambiar su contraseña antes de continuar.")
    if not acceso.permitido(session["role"], request.method, request.url.path):
        raise HTTPException(403, "Su rol no tiene acceso a esta sección.")
    return session


def require(*roles):
    def checker(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Su rol no permite esta acción.")
        return user
    return checker


EDITORS = require("admin", "editor")
ADMINS = require("admin")
INTERNOS = require("admin", "editor", "lector")


def date_or_none(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f"Fecha no válida en {field}.")
