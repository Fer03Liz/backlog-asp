"""Correos diarios a las tiendas y correo de cada tienda."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ....application import administracion, contexto, correos
from ..dependencias import EDITORS, current_user, get_db

router = APIRouter()


@router.get("/api/emails")
def emails(user=Depends(current_user), conn=Depends(get_db)):
    ctx = contexto.Context(conn)
    return {"fecha": ctx.today.isoformat(), "grupos": correos.email_groups(conn, ctx, correos.signature(conn, user))}


class EmailIn(BaseModel):
    formato: str = "texto"   # texto | eml


@router.post("/api/emails/{ship_to}")
def email_register(ship_to: str, body: EmailIn, user=Depends(EDITORS), conn=Depends(get_db)):
    ctx = contexto.Context(conn)
    group = next((g for g in correos.email_groups(conn, ctx, correos.signature(conn, user)) if g["ship_to"] == ship_to), None)
    if not group:
        raise HTTPException(404, "No hay casos para esa tienda.")
    correos.registrar(conn, user, group, body.formato)
    conn.commit()
    if body.formato != "eml":
        return {"ok": True}
    return Response(correos.eml(group), media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{correos.eml_name(group)}"'})


@router.post("/api/emails-lote")
def email_batch(user=Depends(EDITORS), conn=Depends(get_db)):
    """Todos los correos del día en un .zip (solo tiendas con correo registrado)."""
    ctx = contexto.Context(conn)
    groups = [g for g in correos.email_groups(conn, ctx, correos.signature(conn, user)) if g["email"]]
    if not groups:
        raise HTTPException(404, "Ninguna tienda con casos tiene correo registrado.")
    data = correos.zip_del_dia(conn, user, groups)
    conn.commit()
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="correos_{ctx.today.isoformat()}.zip"'})


class ShipToIn(BaseModel):
    email: str | None = Field(None, max_length=320)
    city: str | None = Field(None, max_length=80)


@router.put("/api/shipto/{ship_to}")
def update_shipto(ship_to: str, body: ShipToIn, user=Depends(EDITORS), conn=Depends(get_db)):
    administracion.guardar_tienda(conn, user, ship_to, body.email, body.city)
    conn.commit()
    return {"ok": True}
