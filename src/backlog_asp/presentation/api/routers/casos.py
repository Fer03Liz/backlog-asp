"""Casos, comentarios, guías y exportación."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ....application import acceso, casos, contexto, gestion
from ..dependencias import EDITORS, current_user, date_or_none, get_db, require

router = APIRouter()


@router.get("/api/cases")
def list_cases(incluir_cerrados: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    ctx = contexto.Context(conn)
    return {"hoy": ctx.today.isoformat(), "kgb_fecha": ctx.kgb_date,
            "casos": [acceso.caso(user, c) for c in contexto.load_cases(conn, ctx, include_closed=bool(incluir_cerrados))]}


@router.get("/api/export/cases.csv")
def export_cases(user=Depends(current_user), conn=Depends(get_db)):
    ctx = contexto.Context(conn)
    body = casos.exportar_csv(conn, user, ctx)
    conn.commit()
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="backlog_{ctx.today.isoformat()}.csv"'})


@router.get("/api/cases/{key}")
def case_detail(key: str, user=Depends(current_user), conn=Depends(get_db)):
    return casos.detalle(conn, user, key)


class CaseUpdate(BaseModel):
    estado: str | None = None
    responsable: str | None = Field(None, max_length=80)
    fecha_recepcion: str | None = None
    fecha_despacho: str | None = None
    fecha_objetivo: str | None = None
    propuesta: str | None = None
    aprobacion_kam: str | None = None


@router.patch("/api/cases/{key}")
def update_case(key: str, body: CaseUpdate, user=Depends(EDITORS), conn=Depends(get_db)):
    n = gestion.cambiar_caso(conn, user, key, body.model_dump(exclude_unset=True))
    conn.commit()
    return {"ok": True, "cambios": n}


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    visibilidad: str = "interno"     # interno = solo el equipo; compartido = también lo ve el Apple Care Manager


@router.post("/api/cases/{key}/comments")
def add_comment(key: str, body: CommentIn, user=Depends(require("admin", "editor", "acm")), conn=Depends(get_db)):
    casos.comentar(conn, user, key, body.text, body.visibilidad)
    conn.commit()
    return {"ok": True}


@router.get("/api/guides")
def guides(user=Depends(current_user), conn=Depends(get_db)):
    return casos.guias(conn)


class ArrivalIn(BaseModel):
    fecha: str


@router.post("/api/guides/{tracking}/arrival")
def guide_arrival(tracking: str, body: ArrivalIn, user=Depends(EDITORS), conn=Depends(get_db)):
    fecha = date_or_none(body.fecha, "fecha")
    if not fecha:
        raise HTTPException(422, "Indique la fecha de llegada.")
    r = casos.llegada_guia(conn, user, tracking, fecha)
    conn.commit()
    return {"ok": True, **r}
