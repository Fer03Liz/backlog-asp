"""Servidor local del tablero de backlog ASP."""
import csv
import hmac
import io
import json
import zipfile
from datetime import date
from email.message import EmailMessage

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, ingest, logic, security

app = FastAPI(title="Backlog ASP", docs_url=None, redoc_url=None, openapi_url=None)
db.init_db()

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------- dependencias
def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


ALLOWED_WITH_PENDING_PW = {"/api/me", "/api/password", "/api/logout"}


def current_user(request: Request, conn=Depends(get_db)):
    token = request.cookies.get(security.COOKIE_NAME)
    session = security.load_session(conn, token) if token else None
    if not session:
        raise HTTPException(401, "Sesión no válida o expirada.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, session["csrf"]):
            raise HTTPException(403, "Solicitud rechazada (token CSRF).")
    if session["must_change_pw"] and request.url.path not in ALLOWED_WITH_PENDING_PW:
        raise HTTPException(403, "Debe cambiar su contraseña antes de continuar.")
    return session


def require(*roles):
    def checker(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Su rol no permite esta acción.")
        return user
    return checker


EDITORS = require("admin", "editor")
ADMINS = require("admin")


def _date_or_none(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f"Fecha no válida en {field}.")


# ---------------------------------------------------------------- páginas
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(config.STATIC_DIR / "login.html")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


# ---------------------------------------------------------------- autenticación
class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@app.post("/api/login")
def login(body: LoginIn, conn=Depends(get_db)):
    generic = HTTPException(401, "Usuario o contraseña incorrectos.")
    user = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not user or not user["active"]:
        security.burn_time(body.password)
        db.audit(conn, None, "login_failed", body.username[:64])
        conn.commit()
        raise generic
    if security.is_locked(user):
        db.audit(conn, dict(user), "login_locked", user["username"])
        conn.commit()
        raise HTTPException(423, "Usuario bloqueado temporalmente por intentos fallidos. Intente más tarde.")
    if not security.verify_password(body.password, user["pw_hash"]):
        security.register_failed_login(conn, user)
        db.audit(conn, dict(user), "login_failed", user["username"])
        conn.commit()
        raise generic
    security.register_successful_login(conn, user["id"])
    token = security.create_session(conn, user["id"])
    db.audit(conn, dict(user), "login", user["username"])
    conn.commit()
    resp = JSONResponse({"ok": True, "must_change_pw": bool(user["must_change_pw"])})
    resp.set_cookie(security.COOKIE_NAME, token, httponly=True, samesite="strict", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_HOURS * 3600, path="/")
    return resp


@app.post("/api/logout")
def logout(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    security.delete_session(conn, request.cookies.get(security.COOKIE_NAME, ""))
    db.audit(conn, user, "logout", user["username"])
    conn.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(security.COOKIE_NAME, path="/")
    return resp


@app.get("/api/me")
def me(user=Depends(current_user), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    last = {r["kind"]: r["file_date"] for r in conn.execute(
        "SELECT kind, MAX(file_date) file_date FROM loads GROUP BY kind")}
    return {"username": user["username"], "full_name": user["full_name"], "role": user["role"],
            "csrf": user["csrf"], "must_change_pw": bool(user["must_change_pw"]), "hoy": ctx.today.isoformat(),
            "ultimas_cargas": last, "estados": config.ESTADOS, "aprobacion": config.APROBACION_KAM,
            "propuestas": config.PROPUESTAS, "roles": config.ROLES, "tolerancia": ctx.tol}


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@app.post("/api/password")
def change_password(body: PasswordIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not security.verify_password(body.current, row["pw_hash"]):
        raise HTTPException(400, "La contraseña actual no es correcta.")
    problem = security.password_problem(body.new, row["username"])
    if problem:
        raise HTTPException(400, problem)
    if body.new == body.current:
        raise HTTPException(400, "La nueva contraseña debe ser distinta.")
    conn.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (security.hash_password(body.new), user["id"]))
    security.delete_user_sessions(conn, user["id"])
    token = security.create_session(conn, user["id"])
    db.audit(conn, user, "password_change", user["username"])
    conn.commit()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(security.COOKIE_NAME, token, httponly=True, samesite="strict", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_HOURS * 3600, path="/")
    return resp


# ---------------------------------------------------------------- casos
@app.get("/api/cases")
def list_cases(incluir_cerrados: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    return {"hoy": ctx.today.isoformat(), "kgb_fecha": ctx.kgb_date,
            "casos": logic.load_cases(conn, ctx, include_closed=bool(incluir_cerrados))}


@app.get("/api/cases/{key}")
def case_detail(key: str, user=Depends(current_user), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "Caso no encontrado.")
    ctx = logic.Context(conn)
    case = logic.enrich(dict(row), ctx)
    pnorm = logic.norm_part(case["assembly"])
    guide_rows = [r for tk in case["tracking_list"] for r in ctx.kgb_by_trk.get(tk, [])]
    rel = {pnorm} | {logic.norm_part(s) for s in case["subs_list"]}
    return {
        "caso": case,
        "historial": [dict(r) for r in conn.execute(
            "SELECT file_date, field, old, new FROM case_history WHERE key_notif=? ORDER BY id DESC", (key,))],
        "comentarios": [dict(r) for r in conn.execute(
            "SELECT username, at, text FROM comments WHERE key_notif=? ORDER BY id DESC", (key,))],
        "gestion": [dict(r) for r in conn.execute(
            "SELECT at, username, detail FROM audit_log WHERE target=? AND action='case_update' ORDER BY id DESC LIMIT 50", (key,))],
        "kgb_guia": [{k: r[k] for k in ("tracking", "prod_id", "prod_desc", "qty", "edd", "current_event", "sla_ind", "status")}
                     for r in guide_rows if r["prod_norm"] in rel],
        "kgb_guia_total": len(guide_rows),
        "kgb_parte": ctx.kgb_by_part.get(pnorm) and {**ctx.kgb_by_part[pnorm], "guides": sorted(ctx.kgb_by_part[pnorm]["guides"])},
    }


class CaseUpdate(BaseModel):
    estado: str | None = None
    responsable: str | None = Field(None, max_length=80)
    fecha_recepcion: str | None = None
    fecha_despacho: str | None = None
    fecha_objetivo: str | None = None
    propuesta: str | None = None
    aprobacion_kam: str | None = None


@app.patch("/api/cases/{key}")
def update_case(key: str, body: CaseUpdate, user=Depends(EDITORS), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "Caso no encontrado.")
    old = dict(row)
    changes = body.model_dump(exclude_unset=True)
    if "estado" in changes and changes["estado"] not in config.ESTADOS:
        raise HTTPException(422, "Estado no válido.")
    if "aprobacion_kam" in changes and changes["aprobacion_kam"] not in config.APROBACION_KAM:
        raise HTTPException(422, "Valor de aprobación no válido.")
    if "propuesta" in changes and (changes["propuesta"] or "") not in config.PROPUESTAS:
        raise HTTPException(422, "Propuesta no válida.")
    for f in ("fecha_recepcion", "fecha_despacho", "fecha_objetivo"):
        if f in changes:
            changes[f] = _date_or_none(changes[f], f)
    if "responsable" in changes:
        changes["responsable"] = (changes["responsable"] or "").strip() or None
    if "propuesta" in changes:
        changes["propuesta"] = changes["propuesta"] or None

    today = logic.today().isoformat()
    if changes.get("estado") == "Despachado" and not (changes.get("fecha_despacho") or old["fecha_despacho"]):
        changes["fecha_despacho"] = today
    if changes.get("estado") == "Recibido" and not (changes.get("fecha_recepcion") or old["fecha_recepcion"]):
        changes["fecha_recepcion"] = today
    if changes.get("fecha_despacho") and "estado" not in changes:
        changes["estado"] = "Despachado"
    if changes.get("estado") == "Pendiente aprobación ACM" and "aprobacion_kam" not in changes:
        changes["aprobacion_kam"] = "Pendiente"
    if "aprobacion_kam" in changes and changes["aprobacion_kam"] != old["aprobacion_kam"]:
        if changes["aprobacion_kam"] == "Pendiente":
            changes["kam_solicitado"], changes["kam_respuesta"] = today, None
        elif changes["aprobacion_kam"] in ("Aprobada", "Rechazada"):
            changes["kam_respuesta"] = today

    diff = {k: {"antes": old[k], "ahora": v} for k, v in changes.items() if old[k] != v}
    if not diff:
        return {"ok": True, "cambios": 0}
    sets = {k: v["ahora"] for k, v in diff.items()} | {"updated_at": db.now_iso()}
    conn.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)} WHERE key_notif=?", [*sets.values(), key])
    db.audit(conn, user, "case_update", key, diff)
    conn.commit()
    return {"ok": True, "cambios": len(diff)}


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.post("/api/cases/{key}/comments")
def add_comment(key: str, body: CommentIn, user=Depends(EDITORS), conn=Depends(get_db)):
    if not conn.execute("SELECT 1 FROM cases WHERE key_notif=?", (key,)).fetchone():
        raise HTTPException(404, "Caso no encontrado.")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "El comentario está vacío.")
    conn.execute("INSERT INTO comments(key_notif, user_id, username, at, text) VALUES(?,?,?,?,?)",
                 (key, user["id"], user["username"], db.now_iso(), text))
    db.audit(conn, user, "comment", key, {"texto": text[:200]})
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- guías
@app.get("/api/guides")
def guides(user=Depends(current_user), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    agg = {}
    for c in logic.load_cases(conn, ctx):
        for tk in c["tracking_list"]:
            g = agg.setdefault(tk, {"guia": tk, "casos": 0, "pendientes": 0, "partes": set(), "tiendas": set(),
                                    "claves": [], "eta_min": None, "retrasada": False, "rojas": 0})
            g["casos"] += 1
            g["claves"].append(c["key_notif"])
            g["pendientes"] += c["pendiente"]
            g["partes"].add(c["assembly"])
            g["tiendas"].add(c["list_name"])
            g["rojas"] += c["nivel"] == "rojo"
            notes = (c["notes_jenny"] or "").lower()
            g["retrasada"] |= "delayed" in notes and tk.lower() in notes
            if c["eta_jenny"] and (g["eta_min"] is None or c["eta_jenny"] < g["eta_min"]):
                g["eta_min"] = c["eta_jenny"]
    out = []
    for tk, g in agg.items():
        rows = ctx.kgb_by_trk.get(tk, [])
        edds = sorted(r["edd"] for r in rows if r["edd"])
        g["retrasada"] |= any("out" in (r["sla_ind"] or "").lower() or (r["current_event"] or "").lower().startswith("shipment delay")
                              for r in rows)
        texto = " ".join([tk, *g["claves"], *g["partes"], *(x or "" for x in g["tiendas"])]).lower()
        out.append({**g, "partes": len(g["partes"]), "tiendas": len(g["tiendas"]), "buscar": texto, "en_kgb": bool(rows),
                    "kgb_lineas": len(rows), "kgb_edd": edds[0] if edds else None,
                    "kgb_evento": next((r["current_event"] for r in rows if r["current_event"]), None),
                    "llegada": ctx.arrivals.get(tk)})
    out.sort(key=lambda g: (g["llegada"] is not None, g["kgb_edd"] or g["eta_min"] or "9999"))
    return {"kgb_fecha": ctx.kgb_date, "guias": out}


class ArrivalIn(BaseModel):
    fecha: str


@app.post("/api/guides/{tracking}/arrival")
def guide_arrival(tracking: str, body: ArrivalIn, user=Depends(EDITORS), conn=Depends(get_db)):
    fecha = _date_or_none(body.fecha, "fecha")
    if not fecha:
        raise HTTPException(422, "Indique la fecha de llegada.")
    rows = [dict(r) for r in conn.execute(
        "SELECT key_notif, tracking, estado, fecha_recepcion FROM cases WHERE source_status='abierto' AND fecha_despacho IS NULL")]
    keys = [r for r in rows if tracking in json.loads(r["tracking"] or "[]")]
    if not keys:
        raise HTTPException(404, "No hay casos abiertos con esa guía.")
    conn.execute("INSERT OR REPLACE INTO guide_arrivals(tracking, arrived_on, user_id, at) VALUES(?,?,?,?)",
                 (tracking, fecha, user["id"], db.now_iso()))
    updated = 0
    for r in keys:
        diff = {}
        if not r["fecha_recepcion"]:
            diff["fecha_recepcion"] = {"antes": None, "ahora": fecha}
        if r["estado"] in ("Nuevo", "En gestión"):
            diff["estado"] = {"antes": r["estado"], "ahora": "Recibido"}
        if diff:
            sets = {k: v["ahora"] for k, v in diff.items()}
            conn.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE key_notif=?",
                         [*sets.values(), db.now_iso(), r["key_notif"]])
            db.audit(conn, user, "case_update", r["key_notif"], {**diff, "origen": f"llegada de guía {tracking}"})
            updated += 1
    db.audit(conn, user, "guide_arrival", tracking, {"fecha": fecha, "casos": len(keys)})
    conn.commit()
    return {"ok": True, "casos": len(keys), "actualizados": updated}


# ---------------------------------------------------------------- correos
def _signature(conn, user):
    sig = db.get_settings(conn).get("email_signature", "")
    return f"{user['full_name']}\n{sig}" if user.get("full_name") else sig


def _eml(group) -> bytes:
    msg = EmailMessage()
    if group["email"]:
        msg["To"] = group["email"]
    if group.get("cc"):
        msg["Cc"] = group["cc"]    
    msg["Subject"] = group["subject"]
    msg["X-Unsent"] = "1"   # Outlook lo abre como borrador listo para enviar
    msg.set_content(group["body"])
    return bytes(msg)


def _eml_name(group) -> str:
    safe = "".join(ch for ch in group["tienda"] if ch.isalnum() or ch in " -_").strip().replace(" ", "_")[:60]
    return f"correo_{safe or group['ship_to']}.eml"


def _register_email(conn, user, group, formato):
    """Guarda la fecha comunicada (solo de los casos con fecha) y deja el envío en la bitácora."""
    for key, f in group["fechas"].items():
        conn.execute("UPDATE cases SET fecha_comunicada=? WHERE key_notif=?", (f, key))
    conn.execute("INSERT INTO emails_log(at, user_id, ship_to, n_cases, keys) VALUES(?,?,?,?,?)",
                 (db.now_iso(), user["id"], group["ship_to"], len(group["casos"]), json.dumps(group["casos"])))
    db.audit(conn, user, "email", group["ship_to"], {"casos": len(group["casos"]), "formato": formato})


@app.get("/api/emails")
def emails(user=Depends(current_user), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    return {"fecha": ctx.today.isoformat(), "grupos": logic.email_groups(conn, ctx, _signature(conn, user))}


class EmailIn(BaseModel):
    formato: str = "texto"   # texto | eml


@app.post("/api/emails/{ship_to}")
def email_register(ship_to: str, body: EmailIn, user=Depends(EDITORS), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    group = next((g for g in logic.email_groups(conn, ctx, _signature(conn, user)) if g["ship_to"] == ship_to), None)
    if not group:
        raise HTTPException(404, "No hay casos para esa tienda.")
    _register_email(conn, user, group, body.formato)
    conn.commit()
    if body.formato != "eml":
        return {"ok": True}
    return Response(_eml(group), media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{_eml_name(group)}"'})


@app.post("/api/emails-lote")
def email_batch(user=Depends(EDITORS), conn=Depends(get_db)):
    """Todos los correos del día en un .zip (solo tiendas con correo registrado)."""
    ctx = logic.Context(conn)
    groups = [g for g in logic.email_groups(conn, ctx, _signature(conn, user)) if g["email"]]
    if not groups:
        raise HTTPException(404, "Ninguna tienda con casos tiene correo registrado.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for g in groups:
            zf.writestr(_eml_name(g), _eml(g))
            _register_email(conn, user, g, "lote")
    conn.commit()
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="correos_{ctx.today.isoformat()}.zip"'})

class ShipToIn(BaseModel):
    email: str | None = Field(None, max_length=320)
    city: str | None = Field(None, max_length=80)


@app.put("/api/shipto/{ship_to}")
def update_shipto(ship_to: str, body: ShipToIn, user=Depends(EDITORS), conn=Depends(get_db)):
    """Cambia el correo (o la ciudad) de una tienda sin borrar la copia (CC) ni el cliente."""
    email = (body.email or "").strip() or None
    if email and not all("@" in e and "." in e.split("@")[-1] for e in email.replace(",", ";").split(";") if e.strip()):
        raise HTTPException(422, "Correo no válido. Separe varios correos con punto y coma.")
    row = conn.execute("SELECT * FROM shipto WHERE ship_to=?", (ship_to,)).fetchone()
    old = dict(row) if row else {}
    name = old.get("name") or (conn.execute("SELECT list_name FROM cases WHERE ship_to=? LIMIT 1", (ship_to,)).fetchone() or {"list_name": None})["list_name"]
    city = (body.city or "").strip() or old.get("city")
    primero = (email or "").replace(",", ";").split(";")[0].strip()
    cliente = primero.split("@")[-1].lower() if "@" in primero else old.get("cliente")
    conn.execute("INSERT OR REPLACE INTO shipto(ship_to, name, email, cc_email, cliente, city, updated_at) VALUES(?,?,?,?,?,?,?)",
                 (ship_to, name, email, old.get("cc_email"), cliente, city, db.now_iso()))
    db.audit(conn, user, "shipto_update", ship_to, {"email": {"antes": old.get("email"), "ahora": email}})
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- indicadores
@app.get("/api/kpis")
def get_kpis(user=Depends(current_user), conn=Depends(get_db)):
    return logic.kpis(conn, logic.Context(conn))


# ---------------------------------------------------------------- cargas
@app.post("/api/upload/{kind}")
async def upload(kind: str, file: UploadFile = File(...), fecha: str | None = Form(None),
                 user=Depends(EDITORS), conn=Depends(get_db)):
    if kind not in ("backlog", "kgb", "precios", "partes", "shipto"):
        raise HTTPException(404, "Tipo de archivo no reconocido.")
    name = (file.filename or "archivo").replace("\\", "/").split("/")[-1][:200]
    if not name.lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise HTTPException(400, "Formato no admitido. Use .xlsx o .csv.")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El archivo supera el tamaño máximo permitido (25 MB).")
    fecha = _date_or_none(fecha, "fecha")
    try:
        if kind in ("backlog", "kgb"):
            db.backup(kind)
        if kind == "backlog":
            result = ingest.load_backlog(conn, data, name, fecha, user)
        elif kind == "kgb":
            result = ingest.load_kgb(conn, data, name, fecha, user)
        elif kind == "precios":
            result = ingest.load_prices(conn, data, name, user)
        elif kind == "partes":
            result = ingest.load_parts(conn, data, name, user)        
        else:
            result = ingest.load_shipto(conn, data, name, user)
        conn.commit()
    except ingest.LoadError as exc:
        conn.rollback()
        raise HTTPException(exc.status, str(exc))
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "resultado": result}


@app.get("/api/loads")
def loads(user=Depends(current_user), conn=Depends(get_db)):
    return [{**dict(r), "summary": json.loads(r["summary"] or "{}")} for r in conn.execute(
        """SELECT l.kind, l.file_name, l.file_date, l.loaded_at, l.rows, l.summary, u.username
           FROM loads l LEFT JOIN users u ON u.id = l.user_id ORDER BY l.id DESC LIMIT 60""")]


TEMPLATES = {
    "precios": ["Part", "Precio de inventario", "Precio de intercambio"],
    "shipto": ["shipto", "shipto_name", "city", "Service manager", "Coordinador"],
}


@app.get("/api/templates/{kind}")
def template(kind: str, user=Depends(current_user)):
    if kind not in TEMPLATES:
        raise HTTPException(404, "Plantilla no encontrada.")
    return Response(",".join(TEMPLATES[kind]) + "\n", media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="plantilla_{kind}.csv"'})


def _cell(v):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/api/export/cases.csv")
def export_cases(user=Depends(current_user), conn=Depends(get_db)):
    ctx = logic.Context(conn)
    cols = ["key_notif", "list_name", "ship_to", "assembly", "description", "option_jenny", "eta_jenny", "eta_first",
            "eta_changes", "llegada", "llegada_fuente", "despacho_posible", "limite_fr", "edad", "estado", "responsable",
            "fecha_recepcion", "fecha_despacho", "fecha_comunicada", "aprobacion_kam", "warranty", "tipo_precio", "precio",
            "repair_strategy", "kgb_match", "kgb_edd", "nivel", "source_status", "closed_on", "close_reason"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols + ["alertas"])
    for c in logic.load_cases(conn, ctx, include_closed=True, closed_days=3650):
        w.writerow([_cell(c.get(k)) for k in cols] + [_cell(" | ".join(a["text"] for a in c["alerts"]))])
    db.audit(conn, user, "export", "cases.csv")
    conn.commit()
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="backlog_{ctx.today.isoformat()}.csv"'})


# ---------------------------------------------------------------- administración
@app.get("/api/users")
def users(user=Depends(ADMINS), conn=Depends(get_db)):
    return [dict(r) for r in conn.execute(
        "SELECT id, username, full_name, role, active, must_change_pw, locked_until, created_at FROM users ORDER BY username")]


class UserIn(BaseModel):
    username: str = Field(max_length=64)
    full_name: str = Field("", max_length=120)
    role: str
    password: str = Field(max_length=256)


@app.post("/api/users")
def create_user(body: UserIn, user=Depends(ADMINS), conn=Depends(get_db)):
    try:
        uid = security.create_user(conn, body.username, body.full_name, body.password, body.role, must_change=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        raise HTTPException(409, "Ese usuario ya existe.")
    db.audit(conn, user, "user_create", body.username, {"rol": body.role})
    conn.commit()
    return {"ok": True, "id": uid}


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    new_password: str | None = Field(None, max_length=256)
    unlock: bool | None = None


@app.patch("/api/users/{uid}")
def update_user(uid: int, body: UserUpdate, user=Depends(ADMINS), conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "Usuario no encontrado.")
    if uid == user["id"] and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(400, "No puede desactivarse ni quitarse el rol de administrador a sí mismo.")
    detail = {}
    if body.role:
        if body.role not in config.ROLES:
            raise HTTPException(422, "Rol no válido.")
        conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
        detail["rol"] = body.role
    if body.active is not None:
        conn.execute("UPDATE users SET active=? WHERE id=?", (int(body.active), uid))
        detail["activo"] = body.active
        if not body.active:
            security.delete_user_sessions(conn, uid)
    if body.new_password:
        problem = security.password_problem(body.new_password, row["username"])
        if problem:
            raise HTTPException(422, problem)
        conn.execute("UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0, locked_until=NULL WHERE id=?",
                     (security.hash_password(body.new_password), uid))
        security.delete_user_sessions(conn, uid)
        detail["clave_restablecida"] = True
    if body.unlock:
        conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
        detail["desbloqueado"] = True
    db.audit(conn, user, "user_update", row["username"], detail)
    conn.commit()
    return {"ok": True}


@app.get("/api/settings")
def get_settings(user=Depends(current_user), conn=Depends(get_db)):
    return {"settings": db.get_settings(conn),
            "festivos_extra": [dict(r) for r in conn.execute("SELECT day, name FROM holidays_extra ORDER BY day")],
            "festivos": sorted(x.isoformat() for y in (logic.today().year, logic.today().year + 1)
                               for x in logic.holidays_co(y) if x >= logic.today())}


class SettingsIn(BaseModel):
    settings: dict[str, str]


@app.put("/api/settings")
def put_settings(body: SettingsIn, user=Depends(ADMINS), conn=Depends(get_db)):
    ints = {"fr_tolerance_days", "despachado_grace_days", "kam_alert_days", "oh_max_days", "email_transit_days"}
    for k, v in body.settings.items():
        if k not in config.DEFAULT_SETTINGS:
            raise HTTPException(422, f"Parámetro desconocido: {k}")
        if k in ints and not (v.isdigit() and 0 <= int(v) <= 30):
            raise HTTPException(422, f"{k} debe ser un número entre 0 y 30.")
        if k == "fr_tolerance_business" and v not in ("0", "1"):
            raise HTTPException(422, "fr_tolerance_business debe ser 0 o 1.")
        if len(v) > 500:
            raise HTTPException(422, f"{k} es demasiado largo.")
    old = db.get_settings(conn)
    for k, v in body.settings.items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (v.strip(), k))
    db.audit(conn, user, "settings", None, {k: {"antes": old.get(k), "ahora": v} for k, v in body.settings.items() if old.get(k) != v})
    conn.commit()
    return {"ok": True}


class HolidayIn(BaseModel):
    day: str
    name: str = Field("", max_length=80)


@app.post("/api/holidays")
def add_holiday(body: HolidayIn, user=Depends(ADMINS), conn=Depends(get_db)):
    day = _date_or_none(body.day, "day")
    conn.execute("INSERT OR REPLACE INTO holidays_extra(day, name) VALUES(?,?)", (day, body.name.strip()))
    db.audit(conn, user, "holiday_add", day, {"nombre": body.name})
    conn.commit()
    return {"ok": True}


@app.delete("/api/holidays/{day}")
def delete_holiday(day: str, user=Depends(ADMINS), conn=Depends(get_db)):
    conn.execute("DELETE FROM holidays_extra WHERE day=?", (day,))
    db.audit(conn, user, "holiday_delete", day)
    conn.commit()
    return {"ok": True}


@app.get("/api/audit")
def audit_log(limit: int = 300, user=Depends(ADMINS), conn=Depends(get_db)):
    limit = max(1, min(limit, 2000))
    return [dict(r) for r in conn.execute(
        "SELECT at, username, action, target, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "No encontrado.")}, status_code=404)
    return RedirectResponse("/")
