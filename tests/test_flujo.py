"""Prueba de punta a punta con los archivos reales. Uso: python tests/test_flujo.py <backlog.xlsx> <kgb.xlsx>"""
import io
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["BACKLOG_DATA_DIR"] = tmp
os.environ.setdefault("BACKLOG_HOY", "2026-09-29")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, security  # noqa: E402
from app.main import app  # noqa: E402

BACKLOG, KGB = sys.argv[1], sys.argv[2]
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
sat = [x for x in cases if x["eta_jenny"] == "2026-10-03"][0]
print("ETA sábado ->", sat["key_notif"], "llegada", sat["llegada"], sat["llegada_fuente"], "despacho", sat["despacho_posible"], "límite", sat["limite_fr"])

# Gestión
key = sat["key_notif"]
assert c.patch(f"/api/cases/{key}", json={"estado": "En gestión", "responsable": "Fer"}, headers=H).status_code == 200
assert c.post(f"/api/cases/{key}/comments", json={"text": "Confirmado con bodega <b>ok</b>"}, headers=H).status_code == 200
det = c.get(f"/api/cases/{key}").json()
print("detalle:", det["caso"]["estado"], len(det["comentarios"]), "filas KGB guía:", len(det["kgb_guia"]))

# Guía
g = c.get("/api/guides").json()["guias"]
print("guías:", len(g), "en KGB:", sum(x["en_kgb"] for x in g))
trk = next(x for x in g if x["en_kgb"])["guia"]
r = c.post(f"/api/guides/{trk}/arrival", json={"fecha": "2026-09-29"}, headers=H)
print("llegada guía", trk, r.json())

# Correos
em = c.get("/api/emails", params={"modo": "gestionados"}).json()
print("correos gestionados hoy:", len(em["grupos"]))
print(em["grupos"][0]["body"][:400])
ship = em["grupos"][0]["ship_to"]
assert c.put(f"/api/shipto/{ship}", json={"email": "tienda@ejemplo.com"}, headers=H).status_code == 200
r = c.post(f"/api/emails/{ship}", json={"modo": "gestionados", "formato": "eml"}, headers=H)
print("eml:", r.status_code, r.headers["content-type"], r.content[:120])

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
r = c.post("/api/upload/backlog", files={"file": ("backlog_report_460241_2026_09_29.xlsx", out.getvalue())}, headers=H)
print("día 2:", r.json())
r = c.post("/api/upload/backlog", files={"file": ("backlog_report_460241_2026_09_27.xlsx", out.getvalue() + b" ")}, headers=H)
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
