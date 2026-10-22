"""Ingreso, salida, perfil y cambio de contraseña."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .... import config
from ....application import acceso, sesiones
from ....application.auditoria import audit
from ....domain import catalogos
from ....domain.calendario import today
from ..dependencias import COOKIE_NAME, current_user, get_db

router = APIRouter()


def _con_cookie(body: dict, token: str) -> JSONResponse:
    resp = JSONResponse(body)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_HOURS * 3600, path="/")
    return resp


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/api/login")
def login(body: LoginIn, conn=Depends(get_db)):
    token, user = sesiones.login(conn, body.username, body.password)
    return _con_cookie({"ok": True, "must_change_pw": bool(user["must_change_pw"])}, token)


@router.post("/api/logout")
def logout(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    sesiones.delete_session(conn, request.cookies.get(COOKIE_NAME, ""))
    audit(conn, user, "logout", user["username"])
    conn.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/api/me")
def me(user=Depends(current_user), conn=Depends(get_db)):
    cargas, tol = conn.select_many([("SELECT kind, MAX(file_date) file_date FROM loads GROUP BY kind", ()),
                                    ("SELECT value FROM settings WHERE [key]='fr_tolerance_days'", ())])
    last = {r["kind"]: r["file_date"] for r in cargas}
    return {"username": user["username"], "full_name": user["full_name"], "role": user["role"],
            "rol_nombre": acceso.NOMBRES.get(user["role"], user["role"]), **acceso.perfil(user["role"]),
            "csrf": user["csrf"], "must_change_pw": bool(user["must_change_pw"]), "hoy": today().isoformat(),
            "ultimas_cargas": last, "estados": catalogos.ESTADOS, "aprobacion": catalogos.APROBACION_KAM,
            "propuestas": catalogos.PROPUESTAS, "roles": catalogos.ROLES, "tolerancia": int(tol[0]["value"]) if tol else 2}


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@router.post("/api/password")
def change_password(body: PasswordIn, user=Depends(current_user), conn=Depends(get_db)):
    token = sesiones.change_password(conn, user, body.current, body.new)
    conn.commit()
    return _con_cookie({"ok": True}, token)
