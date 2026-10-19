"""Prueba de punta a punta con archivos reales (base temporal, no toca data/).

Uso: python tests/test_flujo.py <backlog.xlsx> <kgb.xlsx>
Opcional: BACKLOG_HOY=AAAA-MM-DD fija la fecha de "hoy" (por defecto, el día siguiente al backlog).
"""
import io
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["BACKLOG_DATA_DIR"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, security  # noqa: E402
from app.main import app  # noqa: E402

BACKLOG, KGB = sys.argv[1], sys.argv[2]
_m = __import__("re").search(r"(20\d{2})[_\-.](\d{2})[_\-.](\d{2})", Path(BACKLOG).name)
F0 = date(*map(int, _m.groups())) if _m else date.today()
os.environ.setdefault("BACKLOG_HOY", (F0 + timedelta(days=1)).isoformat())
conn = db.connect()
security.create_user(conn, "admin.test", "Admin Prueba", "ClaveSegura2026", "admin", must_change=False)
security.create_user(conn, "lector.test", "Lector", "ClaveLectura2026", "lector", must_change=False)
conn.commit()
conn.close()

c = TestClient(app)
assert c.get("/api/cases").status_code == 401
r = c.post("/api/login", json={"username": "admin.test", "password": "mala"})
assert r.status_code == 401, r.text
r = c.post("/api/login", json={"username": "admin.test", "password": "ClaveSegura2026"})
assert r.status_code == 200, r.text
me = c.get("/api/me").json()
H = {"X-CSRF-Token": me["csrf"]}

# Sin CSRF se rechaza
with open(BACKLOG, "rb") as f:
    data_b = f.read()
assert c.post("/api/upload/backlog", files={"file": (Path(BACKLOG).name, data_b)}).status_code == 403

r = c.post("/api/upload/backlog", files={"file": (Path(BACKLOG).name, data_b)}, headers=H)
print("backlog:", r.status_code, r.json())
assert r.status_code == 200
assert c.post("/api/upload/backlog", files={"file": (Path(BACKLOG).name, data_b)}, headers=H).status_code == 409

with open(KGB, "rb") as f:
    r = c.post("/api/upload/kgb", files={"file": (Path(KGB).name, f.read())}, headers=H)
print("kgb:", r.status_code, r.json())
assert r.status_code == 200

cases = c.get("/api/cases").json()["casos"]
print("casos abiertos:", len(cases))
print("cruce KGB:", Counter(x["kgb_match"] for x in cases))
print("niveles:", Counter(x["nivel"] for x in cases))
print("alertas:", Counter(a["code"] for x in cases for a in x["alerts"]))
top = cases[0]
print("primero en prioridad:", top["key_notif"], top["option_short"], top["nivel"], [a["code"] for a in top["alerts"]])
sat = next((x for x in cases if x["eta_jenny"] and date.fromisoformat(x["eta_jenny"]).weekday() >= 5), cases[0])
print("caso de prueba (ETA en fin de semana si hay) ->", sat["key_notif"], "llegada", sat["llegada"], sat["llegada_fuente"], "despacho", sat["despacho_posible"], "límite", sat["limite_fr"])

# Gestión
key = sat["key_notif"]
assert c.patch(f"/api/cases/{key}", json={"estado": "En gestión", "responsable": "Torre de control"}, headers=H).status_code == 200
assert c.post(f"/api/cases/{key}/comments", json={"text": "Confirmado con bodega <b>ok</b>"}, headers=H).status_code == 200
det = c.get(f"/api/cases/{key}").json()
print("detalle:", det["caso"]["estado"], len(det["comentarios"]), "filas KGB guía:", len(det["kgb_guia"]))

# Guía
g = c.get("/api/guides").json()["guias"]
print("guías:", len(g), "en KGB:", sum(x["en_kgb"] for x in g))
trk = next(x for x in g if x["en_kgb"])["guia"]
r = c.post(f"/api/guides/{trk}/arrival", json={"fecha": me["hoy"]}, headers=H)
print("llegada guía", trk, r.json())

# Correos diarios
em = c.get("/api/emails").json()
g0 = next(g for g in em["grupos"] if g["conteo"]["fecha"])
print("correos del día:", len(em["grupos"]), "tiendas | ejemplo:", g0["tienda"], g0["conteo"])
print(g0["body"][:500])
ship = g0["ship_to"]
assert c.put(f"/api/shipto/{ship}", json={"email": "tienda@ejemplo.com"}, headers=H).status_code == 200
r = c.post(f"/api/emails/{ship}", json={"formato": "eml"}, headers=H)
print("eml:", r.status_code, r.headers["content-type"])
r = c.post("/api/emails-lote", headers=H)
print("zip:", r.status_code, r.headers["content-type"], len(r.content), "bytes")

# Guías: texto de búsqueda e indicador de tiendas
gs = c.get("/api/guides").json()["guias"]
assert all("buscar" in g for g in gs)
print("tiendas:", c.get("/api/kpis").json()["tiendas"])

# Lector no puede editar
c2 = TestClient(app)
c2.post("/api/login", json={"username": "lector.test", "password": "ClaveLectura2026"})
h2 = {"X-CSRF-Token": c2.get("/api/me").json()["csrf"]}
assert c2.patch(f"/api/cases/{key}", json={"estado": "Despachado"}, headers=h2).status_code == 403

# Despacho y KPIs
assert c.patch(f"/api/cases/{key}", json={"estado": "Despachado"}, headers=H).status_code == 200
k = c.get("/api/kpis").json()
print("kpis:", {x: k[x] for x in ("abiertas_reporte", "pendientes_despacho", "reparaciones", "valor", "fill_rate", "proyeccion", "edad", "oh", "alertas")})

# Segundo día: quitar 10 casos, cambiar ETA de 5, agregar 1 nuevo
b = pd.read_excel(io.BytesIO(data_b), sheet_name=None)
bl = b["Backlog"]
removed = bl["Key_Notif"].iloc[:10].tolist()
bl2 = bl.iloc[10:].copy()
bl2.loc[bl2.index[:5], "Initial ETA Resolution"] = pd.Timestamp("2026-10-20")
new = bl2.iloc[[0]].copy()
new["Key_Notif"] = "99999999999|01"
new["Notifctn"] = 99999999999
bl2 = pd.concat([bl2, new])
out = io.BytesIO()
with pd.ExcelWriter(out) as w:
    bl2.to_excel(w, sheet_name="Backlog", index=False)
    b["Allocated"].assign(Key_Notif=[removed[0]] + b["Allocated"]["Key_Notif"].tolist()[1:]).to_excel(w, sheet_name="Allocated", index=False)
r = c.post("/api/upload/backlog", files={"file": (f"backlog_{F0 + timedelta(days=1):%Y_%m_%d}.xlsx", out.getvalue())}, headers=H)
print("día 2:", r.json())
r = c.post("/api/upload/backlog", files={"file": (f"backlog_{F0 - timedelta(days=2):%Y_%m_%d}.xlsx", out.getvalue() + b" ")}, headers=H)
print("archivo viejo:", r.status_code, r.json()["detail"])
det = c.get(f"/api/cases/{removed[0]}").json()["caso"]
print("cerrado:", det["source_status"], det["close_reason"], det["closed_on"])

# CSV con protección de fórmulas
csv = c.get("/api/export/cases.csv").text
print("csv filas:", csv.count("\n"))

# Bloqueo tras intentos fallidos
for _ in range(5):
    c2.post("/api/login", json={"username": "lector.test", "password": "incorrecta"})
print("bloqueo:", c2.post("/api/login", json={"username": "lector.test", "password": "ClaveLectura2026"}).status_code)

# Roles: editor (torre de control) registra el despacho una vez; cambiarlo exige solicitud al administrador
conn = db.connect()
security.create_user(conn, "editor.test", "Editor", "ClaveEditor2026", "editor", must_change=False)
security.create_user(conn, "acm.test", "ACM", "ClaveAcm20261", "acm", must_change=False)
security.create_user(conn, "tienda.test", "Tienda", "ClaveTienda2026", "tienda", must_change=False)
conn.commit()
conn.close()
def entrar(u, p):
    x = TestClient(app)
    assert x.post("/api/login", json={"username": u, "password": p}).status_code == 200
    return x, {"X-CSRF-Token": x.get("/api/me").json()["csrf"]}
ce, he = entrar("editor.test", "ClaveEditor2026")
k2 = next(x["key_notif"] for x in c.get("/api/cases").json()["casos"] if not x["fecha_despacho"])
assert ce.patch(f"/api/cases/{k2}", json={"fecha_despacho": me["hoy"]}, headers=he).status_code == 200
otra = (date.fromisoformat(me["hoy"]) + timedelta(days=1)).isoformat()
assert ce.patch(f"/api/cases/{k2}", json={"fecha_despacho": otra}, headers=he).status_code == 403
r = ce.post(f"/api/cases/{k2}/solicitudes", json={"campo": "fecha_despacho", "valor_nuevo": otra, "motivo": "Prueba: se digitó mal la fecha"}, headers=he)
assert r.status_code == 200, r.text
assert ce.post(f"/api/solicitudes/{r.json()['id']}/resolver", json={"aprobar": True}, headers=he).status_code == 403
assert c.post(f"/api/solicitudes/{r.json()['id']}/resolver", json={"aprobar": True}, headers=H).status_code == 200
assert c.get(f"/api/cases/{k2}").json()["caso"]["fecha_despacho"] == otra
print("fechas protegidas y solicitud aprobada: ok")

# Roles externos: la ACM deja notas (aviso al equipo) y no ve precios; la tienda solo su pantalla
ca, ha = entrar("acm.test", "ClaveAcm20261")
assert ca.patch(f"/api/cases/{k2}", json={"estado": "Nuevo"}, headers=ha).status_code == 403
assert ca.get("/api/emails").status_code == 403
assert all(x["precio"] is None for x in ca.get("/api/cases").json()["casos"])
assert ca.post(f"/api/cases/{k2}/comments", json={"text": "Nota de prueba de la ACM"}, headers=ha).status_code == 200
assert c.get("/api/avisos").json()["total"] >= 1
ct, ht = entrar("tienda.test", "ClaveTienda2026")
assert ct.get("/api/cases").status_code == 403 and ct.get("/api/tienda/casos").status_code == 200
print("roles ACM y tienda: ok")

# Bitácora no editable
conn = db.connect()
try:
    conn.execute("DELETE FROM audit_log")
    print("ERROR: se pudo borrar la bitácora")
except Exception as e:
    print("bitácora protegida:", e)
print("eventos en bitácora:", conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
print("backups:", os.listdir(os.path.join(tmp, "backups")))
print("OK")
