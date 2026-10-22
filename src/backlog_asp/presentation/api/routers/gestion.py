"""Solicitudes de cambio de fecha, avisos y selección múltiple."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ....application import gestion
from ..dependencias import ADMINS, EDITORS, INTERNOS, get_db

router = APIRouter()


class SolicitudIn(BaseModel):
    campo: str
    valor_nuevo: str | None = None
    motivo: str = Field(max_length=500)


@router.post("/api/cases/{key}/solicitudes")
def solicitud_crear(key: str, body: SolicitudIn, user=Depends(EDITORS), conn=Depends(get_db)):
    sid = gestion.solicitar(conn, user, key, body.campo, body.valor_nuevo, body.motivo)
    conn.commit()
    return {"ok": True, "id": sid}


@router.get("/api/solicitudes")
def solicitud_lista(estado: str | None = None, user=Depends(INTERNOS), conn=Depends(get_db)):
    return gestion.solicitudes(conn, estado)


class ResolverIn(BaseModel):
    aprobar: bool
    respuesta: str | None = Field(None, max_length=500)


@router.post("/api/solicitudes/{sid}/resolver")
def solicitud_resolver(sid: int, body: ResolverIn, user=Depends(ADMINS), conn=Depends(get_db)):
    gestion.resolver(conn, user, sid, body.aprobar, body.respuesta)
    conn.commit()
    return {"ok": True}


@router.get("/api/avisos")
def avisos(user=Depends(INTERNOS), conn=Depends(get_db)):
    return gestion.avisos(conn, user)


class LeidasIn(BaseModel):
    ids: list[int] | None = None
    key: str | None = None


@router.post("/api/avisos/leidas")
def avisos_leidas(body: LeidasIn, user=Depends(INTERNOS), conn=Depends(get_db)):
    n = gestion.marcar_leidas(conn, user, body.ids, body.key)
    conn.commit()
    return {"ok": True, "marcadas": n}


class LoteIn(BaseModel):
    keys: list[str] = Field(max_length=2000)
    cambios: dict = {}
    comentario: str | None = Field(None, max_length=2000)
    visibilidad: str = "interno"


@router.post("/api/lote/vista-previa")
def lote_vista_previa(body: LoteIn, user=Depends(EDITORS), conn=Depends(get_db)):
    return gestion.lote_vista_previa(conn, user, body.keys, body.cambios, body.comentario)


@router.post("/api/lote")
def lote_aplicar(body: LoteIn, user=Depends(EDITORS), conn=Depends(get_db)):
    gestion.lote_vista_previa(conn, user, body.keys, body.cambios, body.comentario)
    r = gestion.lote_aplicar(conn, user, body.keys, body.cambios, body.comentario, body.visibilidad)
    conn.commit()
    return {"ok": True, **r}


@router.get("/api/lotes")
def lotes(user=Depends(EDITORS), conn=Depends(get_db)):
    return gestion.lotes_del_dia(conn, user)


@router.post("/api/lote/{lid}/deshacer")
def lote_deshacer(lid: int, user=Depends(EDITORS), conn=Depends(get_db)):
    r = gestion.lote_deshacer(conn, user, lid)
    conn.commit()
    return {"ok": True, **r}
