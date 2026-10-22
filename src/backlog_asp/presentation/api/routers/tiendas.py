"""Vista de la tienda, asociación de tiendas del backlog con el archivo de tiendas y tiendas por usuario."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....application import acceso, contexto, tiendas
from ..dependencias import ADMINS, current_user, get_db

router = APIRouter()


@router.get("/api/tienda/casos")
def tienda_casos(ship_to: str | None = None, user=Depends(current_user), conn=Depends(get_db)):
    if user["role"] == "tienda":
        ship_tos = acceso.tiendas_de(conn, user["id"])
    elif user["role"] in acceso.INTERNOS and ship_to:      # vista previa del equipo
        ship_tos = [ship_to]
    else:
        raise HTTPException(403, "Indique la tienda.")
    return acceso.casos_tienda(conn, contexto.Context(conn), ship_tos)


@router.get("/api/tiendas-alias")
def tiendas_alias(user=Depends(ADMINS), conn=Depends(get_db)):
    return tiendas.sugerencias(conn)


class AliasIn(BaseModel):
    ship_to: str = Field(max_length=40)
    ship_to_archivo: str = Field(max_length=40)


@router.post("/api/tiendas-alias")
def tiendas_alias_guardar(body: AliasIn, user=Depends(ADMINS), conn=Depends(get_db)):
    try:
        tiendas.asociar(conn, user, body.ship_to, body.ship_to_archivo)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    conn.commit()
    return {"ok": True}


@router.post("/api/tiendas-alias/auto")
def tiendas_alias_auto(user=Depends(ADMINS), conn=Depends(get_db)):
    n = tiendas.asociar_automatico(conn, user)
    conn.commit()
    return {"ok": True, "asociadas": n}


@router.delete("/api/tiendas-alias/{ship_to}")
def tiendas_alias_quitar(ship_to: str, user=Depends(ADMINS), conn=Depends(get_db)):
    tiendas.quitar(conn, user, ship_to)
    conn.commit()
    return {"ok": True}


@router.get("/api/tiendas-disponibles")
def tiendas_disponibles(user=Depends(ADMINS), conn=Depends(get_db)):
    return {"tiendas": acceso.disponibles(conn)}


@router.get("/api/users/{uid}/tiendas")
def user_tiendas(uid: int, user=Depends(ADMINS), conn=Depends(get_db)):
    return {"ship_tos": acceso.tiendas_de(conn, uid)}


class TiendasIn(BaseModel):
    ship_tos: list[str] = Field(default_factory=list, max_length=500)


@router.put("/api/users/{uid}/tiendas")
def user_tiendas_guardar(uid: int, body: TiendasIn, user=Depends(ADMINS), conn=Depends(get_db)):
    row = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "Usuario no encontrado.")
    if row["role"] != "tienda":
        raise HTTPException(422, "Solo los usuarios con rol tienda tienen tiendas asignadas.")
    res = acceso.asignar(conn, user, uid, body.ship_tos)
    conn.commit()
    return {"ok": True, "ship_tos": res}
