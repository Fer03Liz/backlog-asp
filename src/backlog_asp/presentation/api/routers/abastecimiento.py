"""Indicadores, abastecimiento por parte, KGB a Colombia, compras en Miami e inventario propio."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ....application import abastecimiento, acceso, auditoria, contexto, indicadores, inventario, miami
from ..dependencias import EDITORS, current_user, get_db

router = APIRouter()


@router.get("/api/kpis")
def get_kpis(user=Depends(current_user), conn=Depends(get_db)):
    return acceso.kpis(user, indicadores.kpis(conn, contexto.Context(conn)))


@router.get("/api/supply")
def supply(todas: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    return acceso.abastecimiento(user, abastecimiento.por_parte(conn, contexto.Context(conn), incluir_sin_casos=bool(todas)))


@router.get("/api/supply/{part}")
def supply_part(part: str, user=Depends(current_user), conn=Depends(get_db)):
    return abastecimiento.detalle_parte(conn, contexto.Context(conn), part)


@router.get("/api/kgb-colombia")
def kgb_colombia(user=Depends(current_user), conn=Depends(get_db)):
    return abastecimiento.kgb_historial(conn)


class MiamiIn(BaseModel):
    fecha_compra: str | None = None
    eta: str | None = None
    cantidad: float | None = None
    part: str | None = Field(None, max_length=40)
    po: str | None = Field(None, max_length=60)
    notas: str | None = Field(None, max_length=500)


class MiamiUpdate(BaseModel):
    accion: str
    fecha: str | None = None
    motivo: str | None = Field(None, max_length=300)
    eta: str | None = None
    po: str | None = Field(None, max_length=60)
    notas: str | None = Field(None, max_length=500)


@router.get("/api/cases/{key}/miami")
def miami_caso(key: str, user=Depends(current_user), conn=Depends(get_db)):
    return {"compras": miami.listar_caso(conn, key),
            "lead_dias": int(auditoria.get_settings(conn).get("miami_lead_days", "7"))}


@router.post("/api/cases/{key}/miami")
def miami_registrar(key: str, body: MiamiIn, user=Depends(EDITORS), conn=Depends(get_db)):
    res = miami.registrar(conn, key, body.model_dump(), user)
    conn.commit()
    return {"ok": True, **res}


@router.patch("/api/miami/{pid}")
def miami_actualizar(pid: int, body: MiamiUpdate, user=Depends(EDITORS), conn=Depends(get_db)):
    res = miami.actualizar(conn, pid, body.model_dump(exclude_unset=True), user)
    conn.commit()
    return res


@router.get("/api/miami")
def miami_listado(todas: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    return miami.listado(conn, contexto.Context(conn), incluir_cerradas=bool(todas))


@router.get("/api/inventario/resumen")
def inventario_resumen(user=Depends(current_user), conn=Depends(get_db)):
    return inventario.resumen(conn, contexto.Context(conn))
