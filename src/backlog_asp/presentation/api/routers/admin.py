"""Usuarios, parámetros, días no hábiles y bitácora (solo administrador, salvo la lectura de parámetros)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ....application import administracion, sesiones
from ....application.auditoria import audit
from ..dependencias import ADMINS, current_user, date_or_none, get_db

router = APIRouter()


@router.get("/api/users")
def users(user=Depends(ADMINS), conn=Depends(get_db)):
    return sesiones.list_users(conn)


class UserIn(BaseModel):
    username: str = Field(max_length=64)
    full_name: str = Field("", max_length=120)
    role: str
    password: str = Field(max_length=256)


@router.post("/api/users")
def create_user(body: UserIn, user=Depends(ADMINS), conn=Depends(get_db)):
    uid = sesiones.create_user(conn, body.username, body.full_name, body.password, body.role, must_change=True)
    audit(conn, user, "user_create", body.username, {"rol": body.role})
    conn.commit()
    return {"ok": True, "id": uid}


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    new_password: str | None = Field(None, max_length=256)
    unlock: bool | None = None


@router.patch("/api/users/{uid}")
def update_user(uid: int, body: UserUpdate, user=Depends(ADMINS), conn=Depends(get_db)):
    sesiones.update_user(conn, user, uid, body.role, body.active, body.new_password, body.unlock)
    conn.commit()
    return {"ok": True}


@router.get("/api/settings")
def get_settings(user=Depends(current_user), conn=Depends(get_db)):
    return administracion.parametros(conn)


class SettingsIn(BaseModel):
    settings: dict[str, str]


@router.put("/api/settings")
def put_settings(body: SettingsIn, user=Depends(ADMINS), conn=Depends(get_db)):
    administracion.guardar_parametros(conn, user, body.settings)
    conn.commit()
    return {"ok": True}


class HolidayIn(BaseModel):
    day: str
    name: str = Field("", max_length=80)


@router.post("/api/holidays")
def add_holiday(body: HolidayIn, user=Depends(ADMINS), conn=Depends(get_db)):
    administracion.agregar_festivo(conn, user, date_or_none(body.day, "day"), body.name)
    conn.commit()
    return {"ok": True}


@router.delete("/api/holidays/{day}")
def delete_holiday(day: str, user=Depends(ADMINS), conn=Depends(get_db)):
    administracion.quitar_festivo(conn, user, day)
    conn.commit()
    return {"ok": True}


@router.get("/api/audit")
def audit_log(limit: int = 300, user=Depends(ADMINS), conn=Depends(get_db)):
    return administracion.bitacora(conn, limit)
