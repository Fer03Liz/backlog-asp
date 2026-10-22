"""Carga de archivos, historial de cargas y plantillas."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .... import config
from ....application import abastecimiento, administracion, cargas, inventario, tiendas
from ..dependencias import EDITORS, current_user, date_or_none, get_db

router = APIRouter()

CARGADORES = {
    "backlog": lambda conn, data, name, fecha, user: cargas.load_backlog(conn, data, name, fecha, user),
    "kgb": lambda conn, data, name, fecha, user: abastecimiento.load_kgb(conn, data, name, fecha, user),
    "openorders": lambda conn, data, name, fecha, user: abastecimiento.load_open_orders(conn, data, name, fecha, user),
    "inventario": lambda conn, data, name, fecha, user: inventario.load_inventario(conn, data, name, fecha, user),
    "precios": lambda conn, data, name, fecha, user: cargas.load_prices(conn, data, name, user),
    "partes": lambda conn, data, name, fecha, user: cargas.load_parts(conn, data, name, user),
    "shipto": lambda conn, data, name, fecha, user: cargas.load_shipto(conn, data, name, user),
}


def _cargar(conn, kind, data, name, fecha, user) -> dict:
    """Toda la carga en una transacción: si algo falla, la base queda como estaba."""
    result = CARGADORES[kind](conn, data, name, fecha, user)
    if kind in ("backlog", "shipto"):
        result["tiendas_asociadas"] = tiendas.asociar_automatico(conn, user)
    conn.commit()
    return result


@router.post("/api/upload/{kind}")
async def upload(kind: str, file: UploadFile = File(...), fecha: str | None = Form(None),
                 user=Depends(EDITORS), conn=Depends(get_db)):
    if kind not in CARGADORES:
        raise HTTPException(404, "Tipo de archivo no reconocido.")
    name = (file.filename or "archivo").replace("\\", "/").split("/")[-1][:200]
    if not name.lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise HTTPException(400, "Formato no admitido. Use .xlsx o .csv.")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El archivo supera el tamaño máximo permitido (25 MB).")
    fecha = date_or_none(fecha, "fecha")
    try:
        result = await run_in_threadpool(_cargar, conn, kind, data, name, fecha, user)
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "resultado": result}


@router.get("/api/loads")
def loads(user=Depends(current_user), conn=Depends(get_db)):
    return administracion.cargas(conn)


TEMPLATES = {
    "precios": ["Part", "Precio de inventario", "Precio de intercambio"],
    "shipto": ["shipto", "shipto_name", "city", "Service manager", "Coordinador"],
    "inventario": ["Parte", "Cantidad", "Bodega", "Descripción"],
}


@router.get("/api/templates/{kind}")
def template(kind: str, user=Depends(current_user)):
    if kind not in TEMPLATES:
        raise HTTPException(404, "Plantilla no encontrada.")
    return Response("﻿" + ",".join(TEMPLATES[kind]) + "\n", media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="plantilla_{kind}.csv"'})
