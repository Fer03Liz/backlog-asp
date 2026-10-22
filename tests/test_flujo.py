"""Prueba de punta a punta contra SQL Server, en un esquema temporal que se borra al final (no toca `backlog`).

Uso: poetry run pytest
- Por defecto genera un backlog, un KGB, Open Orders, precios, tiendas e inventario de prueba.
- Con BACKLOG_TEST_BACKLOG y BACKLOG_TEST_KGB (rutas a archivos reales) usa esos archivos.
Requiere SQL_SERVER_STRING_CONNECTION en el .env.
"""
import io
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ["BACKLOG_DB_SCHEMA"] = f"backlog_test_{uuid.uuid4().hex[:8]}"
os.environ.setdefault("BACKLOG_HOY", date.today().isoformat())

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backlog_asp import config  # noqa: E402
from backlog_asp.application import sesiones  # noqa: E402
from backlog_asp.infrastructure import database, esquema  # noqa: E402
from backlog_asp.presentation.api.app import create_app  # noqa: E402

pytestmark = pytest.mark.skipif(not config.DATABASE_URL, reason="Sin SQL_SERVER_STRING_CONNECTION")
HOY = date.fromisoformat(os.environ["BACKLOG_HOY"])
F0 = HOY - timedelta(days=1)


def _xlsx(**hojas) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out) as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)
    return out.getvalue()


def _backlog(n=40, desde=0) -> pd.DataFrame:
    filas = []
    opciones = ["In Transit", "OH", "Miami", "To be Defined", "FG"]
    for i in range(desde, desde + n):
        opt = opciones[i % len(opciones)]
        filas.append({
            "Key_Notif": f"{40000000 + i}|01", "Notifctn": 40000000 + i, "Line_item": 1,
            "Assembly": f"661-{40000 + i % 12}", "Description_Assembly": f"Parte de prueba ñ {i % 12}",
            "Created_on": pd.Timestamp(F0 - timedelta(days=i % 20)), "PO_Number": f"PO{i}",
            "Ship_to": f"46{i % 6:04d}", "List_Name": f"ISHOP TIENDA {i % 6}", "Warranty_coverage": "OO" if i % 3 else "LP",
            "Report_date": pd.Timestamp(F0), "Repair Strategy": "Z8.2" if i % 7 == 0 else "Z1",
            "Option": opt, "Initial ETA Resolution": pd.Timestamp(HOY + timedelta(days=(i % 9) - 3)),
            "Comments": f"Guía ACSP{100000 + i % 8} parte 661-{40000 + (i + 1) % 12}" if opt == "In Transit" else "",
            "Notes": "delayed" if i % 11 == 0 else "",
        })
    return pd.DataFrame(filas)


def _kgb() -> pd.DataFrame:
    return pd.DataFrame([{
        "TRACKING_NR": f"ACSP{100000 + i % 8}", "PROD_ID": f"661-{40000 + i % 12}", "EDD": pd.Timestamp(HOY + timedelta(days=i % 4)),
        "CUST_ID": "460241" if i % 5 else "999999", "STATUS_DESC": "In Transit", "CURRENT_EVENT": "Shipment Delay" if i == 3 else "Departed",
        "DELIVERY_ID": f"D{i}", "DELIVERY_QTY": 1 + i % 2, "POSO_NR": f"SO{i % 10}", "POSO_ITEM_NR": f"{10 * (i % 3 + 1)}",
        "SHIP_TO_COUNTRY_CD": "CO" if i % 5 else "MX",
    } for i in range(30)])


def _open_orders() -> pd.DataFrame:
    return pd.DataFrame([{
        "Cust Num": "460241", "Sales Ord#": f"SO{i}", "Item": "10", "Material Num": f"661-{40000 + i % 12}",
        "Remaining Qty": 2, "Status": "Backorder", "Recovery ETA": f"Recovery Date: 2026-Nov-{i % 20 + 1}",
        "Cust. PO num.": "12345" if i % 2 else "CHANNELFOC_X",
    } for i in range(15)])


@pytest.fixture(scope="module")
def app():
    conn = database.connect()
    try:
        esquema.init_db(conn)
        sesiones.create_user(conn, "admin.test", "Admin Prueba", "ClaveSegura2026", "admin", must_change=False)
        sesiones.create_user(conn, "lector.test", "Lector", "ClaveLectura2026", "lector", must_change=False)
        sesiones.create_user(conn, "editor.test", "Editor", "ClaveEditor2026", "editor", must_change=False)
        sesiones.create_user(conn, "acm.test", "ACM", "ClaveAcm20261", "acm", must_change=False)
        sesiones.create_user(conn, "tienda.test", "Tienda", "ClaveTienda2026", "tienda", must_change=False)
        conn.commit()
    finally:
        conn.close()
    yield create_app(init_db=False)
    conn = database.connect()
    try:
        esquema.drop_all(conn)
    finally:
        conn.close()


def entrar(app, u, p):
    x = TestClient(app)
    r = x.post("/api/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return x, {"X-CSRF-Token": x.get("/api/me").json()["csrf"]}


def test_flujo_completo(app):
    c = TestClient(app)
    assert c.get("/api/cases").status_code == 401
    assert c.post("/api/login", json={"username": "admin.test", "password": "mala"}).status_code == 401
    c, H = entrar(app, "admin.test", "ClaveSegura2026")
    me = c.get("/api/me").json()
    assert me["role"] == "admin" and me["hoy"] == HOY.isoformat()

    real_b, real_k = os.environ.get("BACKLOG_TEST_BACKLOG"), os.environ.get("BACKLOG_TEST_KGB")
    if real_b and real_k:
        nombre_b, data_b = Path(real_b).name, Path(real_b).read_bytes()
        nombre_k, data_k = Path(real_k).name, Path(real_k).read_bytes()
    else:
        bl = _backlog()
        nombre_b = f"backlog_{F0:%Y_%m_%d}.xlsx"
        data_b = _xlsx(Backlog=bl, Allocated=pd.DataFrame({"Key_Notif": ["x"]}))
        nombre_k, data_k = f"kgb_{F0:%Y_%m_%d}.xlsx", _xlsx(KGB=_kgb())

    # Cargas: CSRF, archivo repetido y en orden
    assert c.post("/api/upload/backlog", files={"file": (nombre_b, data_b)}).status_code == 403
    r = c.post("/api/upload/backlog", files={"file": (nombre_b, data_b)}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["resultado"]["nuevas"] > 0
    assert c.post("/api/upload/backlog", files={"file": (nombre_b, data_b)}, headers=H).status_code == 409
    r = c.post("/api/upload/kgb", files={"file": (nombre_k, data_k)}, headers=H)
    assert r.status_code == 200, r.text
    if not real_b:
        r = c.post("/api/upload/openorders", files={"file": (f"Colombia-Open Orders Report-{F0:%b}.{F0.day}.{F0.year}.xlsx",
                                                            _xlsx(OO=_open_orders()))}, headers=H)
        assert r.status_code == 200, r.text
        precios = "Part,Precio de inventario,Precio de intercambio\n" + "\n".join(f"661-{40000 + i},{100000 + i},{50000}" for i in range(12))
        assert c.post("/api/upload/precios", files={"file": ("precios.csv", precios.encode())}, headers=H).status_code == 200
        tiendas = "shipto,shipto_name,city,Service manager,Coordinador\n" + "\n".join(
            f"1923{i:03d},ISHOP TIENDA {i},Bogotá,gerente{i}@tienda.com,coord@tienda.com" for i in range(6))
        r = c.post("/api/upload/shipto", files={"file": ("tiendas.csv", tiendas.encode())}, headers=H)
        assert r.status_code == 200 and r.json()["resultado"]["tiendas_asociadas"] == 6, r.text
        inv = "Parte,Cantidad,Bodega\n661-40001,3,BOG\n661-40005,1,BOG\n"
        assert c.post("/api/upload/inventario", files={"file": (f"inv_{F0:%Y_%m_%d}.csv", inv.encode())}, headers=H).status_code == 200

    casos = c.get("/api/cases").json()["casos"]
    assert casos and all("alerts" in x for x in casos)
    assert any(x["kgb_match"].startswith("guia") for x in casos)
    key = next(x["key_notif"] for x in casos if not x["fecha_despacho"])

    # Gestión, comentarios y detalle
    assert c.patch(f"/api/cases/{key}", json={"estado": "En gestión", "responsable": "Torre de control"}, headers=H).status_code == 200
    assert c.post(f"/api/cases/{key}/comments", json={"text": "Confirmado con bodega <b>ok</b> ñ"}, headers=H).status_code == 200
    det = c.get(f"/api/cases/{key}").json()
    assert det["caso"]["estado"] == "En gestión" and det["comentarios"][0]["text"].endswith("ñ") and det["gestion"]

    # Guías y llegada
    g = c.get("/api/guides").json()["guias"]
    trk = next(x for x in g if x["en_kgb"])["guia"]
    assert c.post(f"/api/guides/{trk}/arrival", json={"fecha": me["hoy"]}, headers=H).json()["casos"] >= 1

    # Correos, tiendas, abastecimiento, indicadores, exportación
    em = c.get("/api/emails").json()
    assert em["grupos"]
    ship = em["grupos"][0]["ship_to"]
    assert c.put(f"/api/shipto/{ship}", json={"email": "tienda@ejemplo.com"}, headers=H).status_code == 200
    r = c.post(f"/api/emails/{ship}", json={"formato": "eml"}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("message/rfc822")
    assert c.post("/api/emails-lote", headers=H).status_code == 200
    for url in ("/api/kpis", "/api/supply", "/api/kgb-colombia", "/api/miami", "/api/inventario/resumen", "/api/loads",
                "/api/settings", "/api/audit?limit=50", "/api/avisos", "/api/solicitudes", "/api/tiendas-alias",
                "/api/tiendas-disponibles", "/api/users", "/api/lotes", "/api/templates/precios"):
        assert c.get(url).status_code == 200, url
    parte = casos[0]["assembly"]
    assert c.get(f"/api/supply/{parte}").status_code == 200
    csv = c.get("/api/export/cases.csv").text
    assert csv.count("\n") > len(casos) // 2

    # Parámetros y festivos
    assert c.put("/api/settings", json={"settings": {"fr_tolerance_days": "3"}}, headers=H).status_code == 200
    assert c.get("/api/settings").json()["settings"]["fr_tolerance_days"] == "3"
    assert c.post("/api/holidays", json={"day": "2026-12-31", "name": "Cierre"}, headers=H).status_code == 200
    assert c.delete("/api/holidays/2026-12-31", headers=H).status_code == 200

    # Compras en Miami
    km = next(x["key_notif"] for x in casos if x["option_jenny"] == "Miami")
    r = c.post(f"/api/cases/{km}/miami", json={"fecha_compra": me["hoy"], "cantidad": 1}, headers=H)
    assert r.status_code == 200, r.text
    assert c.post(f"/api/cases/{km}/miami", json={"fecha_compra": me["hoy"]}, headers=H).status_code == 409
    assert c.patch(f"/api/miami/{r.json()['id']}", json={"accion": "recibida"}, headers=H).status_code == 200

    # Selección múltiple y deshacer
    claves = [x["key_notif"] for x in casos[:5]]
    r = c.post("/api/lote", json={"keys": claves, "cambios": {"responsable": "Lote"}, "comentario": "Revisado"}, headers=H)
    assert r.status_code == 200 and r.json()["comentarios"] == 5, r.text
    assert c.post(f"/api/lote/{r.json()['id']}/deshacer", headers=H).json()["casos"] == 5

    # Lector no edita; editor registra el despacho una vez y pide el cambio al administrador
    cl, hl = entrar(app, "lector.test", "ClaveLectura2026")
    assert cl.patch(f"/api/cases/{key}", json={"estado": "Despachado"}, headers=hl).status_code == 403
    ce, he = entrar(app, "editor.test", "ClaveEditor2026")
    k2 = next(x["key_notif"] for x in c.get("/api/cases").json()["casos"] if not x["fecha_despacho"] and x["key_notif"] not in claves)
    assert ce.patch(f"/api/cases/{k2}", json={"fecha_despacho": me["hoy"]}, headers=he).status_code == 200
    otra = (HOY + timedelta(days=1)).isoformat()
    assert ce.patch(f"/api/cases/{k2}", json={"fecha_despacho": otra}, headers=he).status_code == 403
    r = ce.post(f"/api/cases/{k2}/solicitudes", json={"campo": "fecha_despacho", "valor_nuevo": otra,
                                                      "motivo": "Prueba: se digitó mal la fecha"}, headers=he)
    assert r.status_code == 200, r.text
    assert ce.post(f"/api/solicitudes/{r.json()['id']}/resolver", json={"aprobar": True}, headers=he).status_code == 403
    assert c.post(f"/api/solicitudes/{r.json()['id']}/resolver", json={"aprobar": True}, headers=H).status_code == 200
    assert c.get(f"/api/cases/{k2}").json()["caso"]["fecha_despacho"] == otra

    # ACM: notas con aviso al equipo, sin precios; tienda: solo su pantalla
    ca, ha = entrar(app, "acm.test", "ClaveAcm20261")
    assert ca.patch(f"/api/cases/{k2}", json={"estado": "Nuevo"}, headers=ha).status_code == 403
    assert ca.get("/api/emails").status_code == 403
    assert all(x["precio"] is None for x in ca.get("/api/cases").json()["casos"])
    assert ca.post(f"/api/cases/{k2}/comments", json={"text": "Nota de prueba de la ACM"}, headers=ha).status_code == 200
    av = c.get("/api/avisos").json()
    assert av["total"] >= 1
    assert c.post("/api/avisos/leidas", json={"ids": [n["id"] for n in av["notas"]]}, headers=H).json()["marcadas"] >= 1
    uid = next(u["id"] for u in c.get("/api/users").json() if u["username"] == "tienda.test")
    assert c.put(f"/api/users/{uid}/tiendas", json={"ship_tos": [ship]}, headers=H).status_code == 200
    ct, _ = entrar(app, "tienda.test", "ClaveTienda2026")
    assert ct.get("/api/cases").status_code == 403
    assert ct.get("/api/tienda/casos").json()["casos"]

    # Segundo día: salen 10 casos, cambian 5 ETA y entra 1 nuevo
    if not real_b:
        bl2 = pd.concat([bl.iloc[10:].copy(), _backlog(1, desde=900)])
        bl2.loc[bl2.index[:5], "Initial ETA Resolution"] = pd.Timestamp(HOY + timedelta(days=10))
        data2 = _xlsx(Backlog=bl2, Allocated=pd.DataFrame({"Key_Notif": [bl["Key_Notif"].iloc[0]]}))
        r = c.post("/api/upload/backlog", files={"file": (f"backlog_{HOY:%Y_%m_%d}.xlsx", data2)}, headers=H)
        res = r.json()["resultado"]
        assert res["cerradas"] == 10 and res["nuevas"] == 1 and res["asignadas"] == 1 and res["cambios_eta"] >= 1, res
        viejo = c.post("/api/upload/backlog", files={"file": (f"backlog_{F0 - timedelta(days=2):%Y_%m_%d}.xlsx", data2 + b" ")}, headers=H)
        assert viejo.status_code == 409
        cerrado = c.get(f"/api/cases/{bl['Key_Notif'].iloc[0]}").json()["caso"]
        assert cerrado["source_status"] == "cerrado" and cerrado["close_reason"] == "Asignado"

    # Bloqueo tras intentos fallidos
    x = TestClient(app)
    for _ in range(5):
        x.post("/api/login", json={"username": "lector.test", "password": "incorrecta"})
    assert x.post("/api/login", json={"username": "lector.test", "password": "ClaveLectura2026"}).status_code == 423

    # Bitácora y comentarios no se pueden borrar
    conn = database.connect()
    try:
        for tabla in ("audit_log", "comments"):
            with pytest.raises(Exception):
                conn.execute(f"DELETE FROM {tabla}")
            conn.rollback()
        assert conn.execute("SELECT COUNT(*) n FROM audit_log").fetchone()["n"] > 20
    finally:
        conn.close()
