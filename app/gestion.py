"""Gestión del equipo: cambios de un caso, fechas protegidas, solicitudes de cambio, avisos y selección múltiple.

- Fechas de recepción y despacho: un editor las fija una sola vez. Para cambiarlas o borrarlas pide una
  solicitud con justificación y un administrador la aprueba o la rechaza. El administrador sí puede cambiarlas.
- Avisos: cada nota de la Apple Care Manager queda "sin leer" hasta que alguien del equipo la marque leída;
  mientras tanto el caso muestra la alerta NOTA_ACM y la campana cuenta la nota.
- Selección múltiple: aplicar los mismos cambios a varios casos, con vista previa, en una tanda que queda en la
  bitácora y que el mismo usuario (o un administrador) puede deshacer el mismo día.
"""
import json
from datetime import date

from . import config, db, logic

PROTEGIDAS = ("fecha_recepcion", "fecha_despacho")
NOMBRE = {"fecha_recepcion": "fecha de recepción", "fecha_despacho": "fecha de despacho"}
CAMPOS_LOTE = ("estado", "responsable", "fecha_recepcion", "fecha_despacho", "fecha_objetivo", "propuesta", "aprobacion_kam")

SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitudes_fecha(
  id INTEGER PRIMARY KEY, key_notif TEXT NOT NULL,
  campo TEXT NOT NULL CHECK(campo IN ('fecha_recepcion','fecha_despacho')),
  valor_actual TEXT, valor_nuevo TEXT, motivo TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'Pendiente' CHECK(estado IN ('Pendiente','Aprobada','Rechazada')),
  solicitado_por TEXT, solicitado_at TEXT NOT NULL, resuelto_por TEXT, resuelto_at TEXT, respuesta TEXT
);
CREATE INDEX IF NOT EXISTS idx_solicitudes_key ON solicitudes_fecha(key_notif);
CREATE TABLE IF NOT EXISTS notas_leidas(
  comment_id INTEGER PRIMARY KEY, username TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lotes(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, username TEXT,
  cambios TEXT NOT NULL, casos TEXT NOT NULL, comentarios TEXT, deshecho_at TEXT, deshecho_por TEXT
);
"""


class GestionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def init_db() -> None:
    conn = db.connect()
    try:
        nueva = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='notas_leidas'").fetchone()
        conn.executescript(SCHEMA)
        if nueva:
            # Los comentarios no se editan (trigger): la lectura se guarda aparte. Las notas que ya existían
            # antes de los avisos se dan por leídas.
            conn.execute(f"""INSERT OR IGNORE INTO notas_leidas(comment_id, username, at)
                             SELECT m.id, '(anteriores)', ? FROM comments m {_ES_ACM}""", (db.now_iso(),))
        conn.commit()
    finally:
        conn.close()


_ES_ACM = "JOIN users u ON u.id = m.user_id AND u.role = 'acm'"
_SIN_LEER = f"{_ES_ACM} LEFT JOIN notas_leidas l ON l.comment_id = m.id WHERE l.comment_id IS NULL"


def _fecha(valor, campo):
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor)[:10]).isoformat()
    except ValueError:
        raise GestionError(f"Fecha no válida en {campo}.", 422)


# ---------------------------------------------------------------- cambios de un caso
def normalizar(changes: dict) -> dict:
    out = dict(changes)
    if "estado" in out and out["estado"] not in config.ESTADOS:
        raise GestionError("Estado no válido.", 422)
    if "aprobacion_kam" in out and out["aprobacion_kam"] not in config.APROBACION_KAM:
        raise GestionError("Valor de aprobación no válido.", 422)
    if "propuesta" in out and (out["propuesta"] or "") not in config.PROPUESTAS:
        raise GestionError("Propuesta no válida.", 422)
    for f in ("fecha_recepcion", "fecha_despacho", "fecha_objetivo"):
        if f in out:
            out[f] = _fecha(out[f], f)
    if "responsable" in out:
        out["responsable"] = (out["responsable"] or "").strip()[:80] or None
    if "propuesta" in out:
        out["propuesta"] = out["propuesta"] or None
    return out


def protegidas_bloqueadas(user, old: dict, changes: dict) -> list:
    """Fechas que este usuario no puede cambiar porque ya estaban registradas."""
    if user["role"] == "admin":
        return []
    return [f for f in PROTEGIDAS if f in changes and old[f] and changes[f] != old[f]]


def calcular(old: dict, changes: dict) -> dict:
    """Reglas automáticas entre campos. Devuelve el diff {campo: {antes, ahora}}."""
    changes = dict(changes)
    today = logic.today().isoformat()
    if changes.get("estado") == "Despachado" and not (changes.get("fecha_despacho") or old["fecha_despacho"]):
        changes["fecha_despacho"] = today
    if changes.get("estado") == "Recibido" and not (changes.get("fecha_recepcion") or old["fecha_recepcion"]):
        changes["fecha_recepcion"] = today
    sin_cambio_estado = changes.get("estado", old["estado"]) == old["estado"]   # el formulario siempre envía el estado
    if changes.get("fecha_despacho") and not old["fecha_despacho"] and sin_cambio_estado:
        changes["estado"] = "Despachado"
    elif changes.get("fecha_recepcion") and not old["fecha_recepcion"] and sin_cambio_estado and old["estado"] in ("Nuevo", "En gestión"):
        changes["estado"] = "Recibido"
    if changes.get("estado") == "Pendiente aprobación ACM" and "aprobacion_kam" not in changes:
        changes["aprobacion_kam"] = "Pendiente"
    if "aprobacion_kam" in changes and changes["aprobacion_kam"] != old["aprobacion_kam"]:
        if changes["aprobacion_kam"] == "Pendiente":
            changes["kam_solicitado"], changes["kam_respuesta"] = today, None
        elif changes["aprobacion_kam"] in ("Aprobada", "Rechazada"):
            changes["kam_respuesta"] = today
    return {k: {"antes": old[k], "ahora": v} for k, v in changes.items() if old[k] != v}


def guardar(conn, user, key: str, diff: dict, origen: str = None) -> None:
    if not diff:
        return
    sets = {k: v["ahora"] for k, v in diff.items()} | {"updated_at": db.now_iso()}
    conn.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)} WHERE key_notif=?", [*sets.values(), key])
    db.audit(conn, user, "case_update", key, {**diff, **({"origen": origen} if origen else {})})


def cambiar_caso(conn, user, key: str, body: dict) -> int:
    row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not row:
        raise GestionError("Caso no encontrado.", 404)
    old = dict(row)
    changes = normalizar(body)
    bloq = protegidas_bloqueadas(user, old, changes)
    if bloq:
        nombres = " y ".join(NOMBRE[f] for f in bloq)
        raise GestionError(f"La {nombres} ya está registrada. Para cambiarla, use «Solicitar cambio» y justifique; "
                           "un administrador la aprueba.", 403)
    diff = calcular(old, changes)
    guardar(conn, user, key, diff)
    return len(diff)


# ---------------------------------------------------------------- solicitudes de cambio de fecha
def solicitar(conn, user, key: str, campo: str, valor_nuevo, motivo: str) -> int:
    if campo not in PROTEGIDAS:
        raise GestionError("Solo se solicitan cambios de la fecha de recepción o de despacho.", 422)
    row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
    if not row:
        raise GestionError("Caso no encontrado.", 404)
    actual = row[campo]
    nuevo = _fecha(valor_nuevo, campo)
    motivo = (motivo or "").strip()
    if not actual:
        raise GestionError(f"El caso no tiene {NOMBRE[campo]}: regístrela directamente.", 409)
    if nuevo == actual:
        raise GestionError("La fecha nueva es igual a la actual.", 422)
    if len(motivo) < 10:
        raise GestionError("Explique el motivo del cambio (al menos 10 caracteres).", 422)
    if conn.execute("SELECT 1 FROM solicitudes_fecha WHERE key_notif=? AND campo=? AND estado='Pendiente'", (key, campo)).fetchone():
        raise GestionError("Ya hay una solicitud pendiente para esta fecha.", 409)
    cur = conn.execute(
        """INSERT INTO solicitudes_fecha(key_notif, campo, valor_actual, valor_nuevo, motivo, solicitado_por, solicitado_at)
           VALUES(?,?,?,?,?,?,?)""", (key, campo, actual, nuevo, motivo[:500], user["username"], db.now_iso()))
    db.audit(conn, user, "solicitud_fecha", key, {"id": cur.lastrowid, "campo": campo, "antes": actual, "ahora": nuevo, "motivo": motivo[:200]})
    return cur.lastrowid


def solicitudes(conn, estado: str = None, key: str = None, dias: int = 30) -> list:
    sql, args = "SELECT s.*, c.list_name, c.assembly FROM solicitudes_fecha s LEFT JOIN cases c ON c.key_notif=s.key_notif WHERE 1=1", []
    if estado:
        sql += " AND s.estado=?"; args.append(estado)
    if key:
        sql += " AND s.key_notif=?"; args.append(key)
    elif not estado or estado != "Pendiente":
        sql += " AND s.solicitado_at >= date('now', ?)"; args.append(f"-{dias} days")
    return [dict(r) for r in conn.execute(sql + " ORDER BY s.id DESC", args)]


def resolver(conn, admin, sid: int, aprobar: bool, respuesta: str) -> None:
    s = conn.execute("SELECT * FROM solicitudes_fecha WHERE id=?", (sid,)).fetchone()
    if not s:
        raise GestionError("Solicitud no encontrada.", 404)
    if s["estado"] != "Pendiente":
        raise GestionError(f"La solicitud ya está {s['estado'].lower()}.", 409)
    respuesta = (respuesta or "").strip()[:500] or None
    if not aprobar and not respuesta:
        raise GestionError("Indique por qué se rechaza.", 422)
    if aprobar:
        old = dict(conn.execute("SELECT * FROM cases WHERE key_notif=?", (s["key_notif"],)).fetchone())
        diff = {s["campo"]: {"antes": old[s["campo"]], "ahora": s["valor_nuevo"]}} if old[s["campo"]] != s["valor_nuevo"] else {}
        if s["campo"] == "fecha_despacho" and s["valor_nuevo"] is None and old["estado"] == "Despachado":
            diff["estado"] = {"antes": "Despachado", "ahora": "Recibido" if old["fecha_recepcion"] else "En gestión"}
        guardar(conn, admin, s["key_notif"], diff, f"solicitud #{sid} de {s['solicitado_por']}: {s['motivo'][:120]}")
    conn.execute("UPDATE solicitudes_fecha SET estado=?, resuelto_por=?, resuelto_at=?, respuesta=? WHERE id=?",
                 ("Aprobada" if aprobar else "Rechazada", admin["username"], db.now_iso(), respuesta, sid))
    db.audit(conn, admin, "solicitud_resuelta", s["key_notif"], {"id": sid, "aprobada": aprobar, "respuesta": respuesta})


# ---------------------------------------------------------------- avisos (notas de la ACM)
def cargar_contexto(ctx, conn) -> None:
    ctx.notas_acm = {}
    try:
        for r in conn.execute(f"SELECT m.key_notif, m.username, m.at, m.text FROM comments m {_SIN_LEER} ORDER BY m.id"):
            n = ctx.notas_acm.setdefault(r["key_notif"], {"n": 0})
            n.update(n=n["n"] + 1, username=r["username"], at=r["at"], text=r["text"])
    except Exception:
        pass
    ctx.gestion_alertas = alertas


def alertas(c, ctx) -> list:
    n = ctx.notas_acm.get(c["key_notif"])
    if not n:
        return []
    extra = f" (y {n['n'] - 1} más)" if n["n"] > 1 else ""
    texto = n["text"] if len(n["text"]) <= 120 else n["text"][:117] + "…"
    return [{"code": "NOTA_ACM", "level": "ambar",
             "text": f"Nota nueva de la ACM ({n['username']}, {n['at'][:10]}){extra}: «{texto}»"}]


def avisos(conn, user) -> dict:
    notas = [dict(r) for r in conn.execute(
        f"""SELECT m.id, m.key_notif, m.username, m.at, m.text, c.list_name, c.assembly FROM comments m
            LEFT JOIN cases c ON c.key_notif=m.key_notif {_SIN_LEER} ORDER BY m.id DESC LIMIT 200""")]
    out = {"notas": notas, "solicitudes": [], "mis_solicitudes": []}
    if user["role"] == "admin":
        out["solicitudes"] = solicitudes(conn, "Pendiente")
    out["mis_solicitudes"] = [s for s in solicitudes(conn, dias=7) if s["solicitado_por"] == user["username"] and s["estado"] != "Pendiente"]
    out["total"] = len(out["notas"]) + len(out["solicitudes"])
    return out


def marcar_leidas(conn, user, ids: list = None, key: str = None) -> int:
    sql, args = f"SELECT m.id FROM comments m {_SIN_LEER}", []
    if ids:
        sql += f" AND m.id IN ({','.join('?' * len(ids))})"; args += [int(i) for i in ids]
    elif key:
        sql += " AND m.key_notif=?"; args.append(key)
    pend = [r["id"] for r in conn.execute(sql, args)]
    now = db.now_iso()
    conn.executemany("INSERT OR IGNORE INTO notas_leidas(comment_id, username, at) VALUES(?,?,?)",
                     [(i, user["username"], now) for i in pend])
    n = len(pend)
    if n:
        db.audit(conn, user, "notas_leidas", key or ",".join(map(str, ids or [])) or "todas", {"cantidad": n})
    return n


# ---------------------------------------------------------------- selección múltiple
def _plan(conn, user, keys: list, cambios: dict):
    cambios = normalizar({k: v for k, v in cambios.items() if k in CAMPOS_LOTE})
    plan, omitidos = [], []
    for key in dict.fromkeys(keys):
        row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (key,)).fetchone()
        if not row:
            omitidos.append({"key": key, "motivo": "no existe"})
            continue
        old = dict(row)
        propios = dict(cambios)
        bloq = protegidas_bloqueadas(user, old, propios)
        for f in bloq:
            propios.pop(f)
            omitidos.append({"key": key, "motivo": f"{NOMBRE[f]} ya registrada ({old[f]}); requiere solicitud"})
        diff = calcular(old, propios)
        plan.append({"key": key, "tienda": old["list_name"], "parte": old["assembly"], "diff": diff})
    return cambios, plan, omitidos


def lote_vista_previa(conn, user, keys: list, cambios: dict, comentario: str = None) -> dict:
    if not keys:
        raise GestionError("No hay casos seleccionados.", 422)
    if len(keys) > 2000:
        raise GestionError("Máximo 2.000 casos por tanda.", 422)
    cambios, plan, omitidos = _plan(conn, user, keys, cambios)
    if not cambios and not (comentario or "").strip():
        raise GestionError("Indique al menos un cambio o un comentario.", 422)
    return {"casos": len(plan), "con_cambios": sum(1 for p in plan if p["diff"]),
            "sin_cambios": sum(1 for p in plan if not p["diff"]), "omitidos": omitidos,
            "detalle": plan[:300], "comentario": bool((comentario or "").strip())}


def lote_aplicar(conn, user, keys: list, cambios: dict, comentario: str = None, visibilidad: str = "interno") -> dict:
    cambios, plan, omitidos = _plan(conn, user, keys, cambios)
    comentario = (comentario or "").strip()[:2000]
    if visibilidad not in ("interno", "compartido"):
        raise GestionError("Visibilidad no válida.", 422)
    now = db.now_iso()
    cur = conn.execute("INSERT INTO lotes(at, user_id, username, cambios, casos) VALUES(?,?,?,?,?)",
                       (now, user["id"], user["username"], json.dumps({**cambios, "comentario": comentario or None}, ensure_ascii=False), "[]"))
    lid = cur.lastrowid
    hechos, coment_ids = [], []
    for p in plan:
        if p["diff"]:
            guardar(conn, user, p["key"], p["diff"], f"selección múltiple #{lid}")
            hechos.append({"key": p["key"], "diff": p["diff"]})
        if comentario:
            c = conn.execute("INSERT INTO comments(key_notif, user_id, username, at, text, visibilidad) VALUES(?,?,?,?,?,?)",
                             (p["key"], user["id"], user["username"], now, f"{comentario} (selección múltiple #{lid})", visibilidad))
            coment_ids.append(c.lastrowid)
    conn.execute("UPDATE lotes SET casos=?, comentarios=? WHERE id=?",
                 (json.dumps(hechos, ensure_ascii=False), json.dumps(coment_ids), lid))
    db.audit(conn, user, "lote", str(lid), {"casos": len(plan), "con_cambios": len(hechos), "comentarios": len(coment_ids),
                                            "omitidos": len(omitidos)})
    return {"id": lid, "con_cambios": len(hechos), "comentarios": len(coment_ids), "omitidos": omitidos}


def lotes_del_dia(conn, user) -> list:
    hoy = logic.today().isoformat()
    filas = conn.execute("SELECT * FROM lotes WHERE substr(at,1,10)=? ORDER BY id DESC", (hoy,))
    out = []
    for r in filas:
        if user["role"] != "admin" and r["user_id"] != user["id"]:
            continue
        out.append({"id": r["id"], "at": r["at"], "username": r["username"], "cambios": json.loads(r["cambios"]),
                    "casos": len(json.loads(r["casos"])), "comentarios": len(json.loads(r["comentarios"] or "[]")),
                    "deshecho_at": r["deshecho_at"], "deshecho_por": r["deshecho_por"]})
    return out


def lote_deshacer(conn, user, lid: int) -> dict:
    r = conn.execute("SELECT * FROM lotes WHERE id=?", (lid,)).fetchone()
    if not r:
        raise GestionError("Tanda no encontrada.", 404)
    if r["deshecho_at"]:
        raise GestionError("Esa tanda ya se deshizo.", 409)
    if user["role"] != "admin" and r["user_id"] != user["id"]:
        raise GestionError("Solo quien aplicó la tanda o un administrador puede deshacerla.", 403)
    if r["at"][:10] != logic.today().isoformat():
        raise GestionError("Solo se puede deshacer el mismo día.", 409)
    revertidos, saltados = 0, 0
    for h in json.loads(r["casos"]):
        row = conn.execute("SELECT * FROM cases WHERE key_notif=?", (h["key"],)).fetchone()
        if not row:
            continue
        cur = dict(row)
        diff = {}
        for campo, v in h["diff"].items():
            if cur[campo] == v["ahora"]:              # no se toca lo que alguien cambió después
                diff[campo] = {"antes": v["ahora"], "ahora": v["antes"]}
            else:
                saltados += 1
        if diff:
            guardar(conn, user, h["key"], diff, f"deshacer selección múltiple #{lid}")
            revertidos += 1
    ids = json.loads(r["comentarios"] or "[]")       # los comentarios no se borran (bitácora): quedan
    conn.execute("UPDATE lotes SET deshecho_at=?, deshecho_por=? WHERE id=?", (db.now_iso(), user["username"], lid))
    db.audit(conn, user, "lote_deshecho", str(lid), {"casos": revertidos, "campos_saltados": saltados})
    return {"casos": revertidos, "campos_saltados": saltados, "comentarios_quedan": len(ids)}
