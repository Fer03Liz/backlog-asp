"""Asociación de las tiendas del backlog con el archivo de tiendas (la vista de la tienda se publica en Power BI)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....application import tiendas
from ..dependencias import CORREOS, get_db

router = APIRouter()


@router.get("/api/tiendas-alias")
def tiendas_alias(user=Depends(CORREOS), conn=Depends(get_db)):
    return tiendas.sugerencias(conn)


class AliasIn(BaseModel):
    ship_to: str = Field(max_length=40)
    ship_to_archivo: str = Field(max_length=40)


@router.post("/api/tiendas-alias")
def tiendas_alias_guardar(body: AliasIn, user=Depends(CORREOS), conn=Depends(get_db)):
    try:
        tiendas.asociar(conn, user, body.ship_to, body.ship_to_archivo)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    conn.commit()
    return {"ok": True}


@router.post("/api/tiendas-alias/auto")
def tiendas_alias_auto(user=Depends(CORREOS), conn=Depends(get_db)):
    n = tiendas.asociar_automatico(conn, user)
    conn.commit()
    return {"ok": True, "asociadas": n}


@router.delete("/api/tiendas-alias/{ship_to}")
def tiendas_alias_quitar(ship_to: str, user=Depends(CORREOS), conn=Depends(get_db)):
    tiendas.quitar(conn, user, ship_to)
    conn.commit()
    return {"ok": True}
