"""Gestión del equipo: cambios de un caso, fechas protegidas, solicitudes de cambio, avisos y selección múltiple.

- Fechas de recepción y despacho: un editor las fija una sola vez. Para cambiarlas o borrarlas pide una
  solicitud con justificación y un administrador la aprueba o la rechaza. El administrador sí puede cambiarlas.
- Avisos: cada nota de la Apple Care Manager queda "sin leer" hasta que alguien del equipo la marque leída;
  mientras tanto el caso muestra la alerta NOTA_ACM y la campana cuenta la nota.
- Selección múltiple: aplicar los mismos cambios a varios casos, con vista previa, en una tanda que queda en la
  bitácora y que el mismo usuario (o un administrador) puede deshacer el mismo día.
"""
import json
from datetime import timedelta

from ..domain.calendario import today
from ..domain.errores import GestionError
from ..domain.gestion import CAMPOS_LOTE, NOMBRE, PROTEGIDAS, calcular, fecha, normalizar, protegidas_bloqueadas
from .auditoria import audit, now_iso

_SIN_LEER = """JOIN users u ON u.id = m.user_id AND u.role = 'acm'
               LEFT JOIN notas_leidas l ON l.comment_id = m.id WHERE l.comment_id IS NULL"""


def guardar(conn, user, key: str, diff: dict, origen: str = None) -> None:
    if not diff:
        return
    sets = {k: v["ahora"] for k, v in diff.items()} | {"updated_at": now_iso()}
    conn.defer(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)} WHERE key_notif=?", [*sets.values(), key])
    audit(conn, user, "case_update", key, {**diff, **({"origen": origen} if origen else {})})


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
    row, pendiente = conn.select_many([
        ("SELECT * FROM cases WHERE key_notif=?", (key,)),
        ("SELECT 1 x FROM solicitudes_fecha WHERE key_notif=? AND campo=? AND estado='Pendiente'", (key, campo)),
    ])
    if not row:
        raise GestionError("Caso no encontrado.", 404)
    actual = row[0][campo]
    nuevo = fecha(valor_nuevo, campo)
    motivo = (motivo or "").strip()
    if not actual:
        raise GestionError(f"El caso no tiene {NOMBRE[campo]}: regístrela directamente.", 409)
    if nuevo == actual:
        raise GestionError("La fecha nueva es igual a la actual.", 422)
    if len(motivo) < 10:
        raise GestionError("Explique el motivo del cambio (al menos 10 caracteres).", 422)
    if pendiente:
        raise GestionError("Ya hay una solicitud pendiente para esta fecha.", 409)
    cur = conn.execute(
        """INSERT INTO solicitudes_fecha(key_notif, campo, valor_actual, valor_nuevo, motivo, solicitado_por, solicitado_at)
           VALUES(?,?,?,?,?,?,?)""", (key, campo, actual, nuevo, motivo[:500], user["username"], now_iso()))
    audit(conn, user, "solicitud_fecha", key, {"id": cur.lastrowid, "campo": campo, "antes": actual, "ahora": nuevo, "motivo": motivo[:200]})
    return cur.lastrowid


def _consulta_solicitudes(estado: str = None, key: str = None, dias: int = 30):
    sql, args = "SELECT s.*, c.list_name, c.assembly FROM solicitudes_fecha s LEFT JOIN cases c ON c.key_notif=s.key_notif WHERE 1=1", []
    if estado:
        sql += " AND s.estado=?"
        args.append(estado)
    if key:
        sql += " AND s.key_notif=?"
        args.append(key)
    elif not estado or estado != "Pendiente":
        sql += " AND s.solicitado_at >= ?"
        args.append((today() - timedelta(days=dias)).isoformat())
    return sql + " ORDER BY s.id DESC", args


def solicitudes(conn, estado: str = None, key: str = None, dias: int = 30) -> list:
    return [dict(r) for r in conn.execute(*_consulta_solicitudes(estado, key, dias))]


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
    conn.defer("UPDATE solicitudes_fecha SET estado=?, resuelto_por=?, resuelto_at=?, respuesta=? WHERE id=?",
               ("Aprobada" if aprobar else "Rechazada", admin["username"], now_iso(), respuesta, sid))
    audit(conn, admin, "solicitud_resuelta", s["key_notif"], {"id": sid, "aprobada": aprobar, "respuesta": respuesta})


# ---------------------------------------------------------------- avisos (notas de la ACM)
def avisos(conn, user) -> dict:
    consultas = [(f"""SELECT TOP 200 m.id, m.key_notif, m.username, m.at, m.text, c.list_name, c.assembly FROM comments m
                      LEFT JOIN cases c ON c.key_notif=m.key_notif {_SIN_LEER} ORDER BY m.id DESC""", ()),
                 _consulta_solicitudes(dias=7)]
    if user["role"] == "admin":
        consultas.append(_consulta_solicitudes("Pendiente"))
    filas = conn.select_many(consultas)
    out = {"notas": [dict(r) for r in filas[0]], "solicitudes": [dict(r) for r in filas[2]] if len(filas) > 2 else [],
           "mis_solicitudes": [dict(s) for s in filas[1] if s["solicitado_por"] == user["username"] and s["estado"] != "Pendiente"]}
    out["total"] = len(out["notas"]) + len(out["solicitudes"])
    return out


def marcar_leidas(conn, user, ids: list = None, key: str = None) -> int:
    sql, args = f"SELECT m.id FROM comments m {_SIN_LEER}", []
    if ids:
        sql += f" AND m.id IN ({','.join('?' * len(ids))})"
        args += [int(i) for i in ids]
    elif key:
        sql += " AND m.key_notif=?"
        args.append(key)
    pend = [r["id"] for r in conn.execute(sql, args)]
    now = now_iso()
    conn.executemany("""INSERT INTO notas_leidas(comment_id, username, at) SELECT ?, ?, ?
                        WHERE NOT EXISTS (SELECT 1 FROM notas_leidas WHERE comment_id=?)""",
                     [(i, user["username"], now, i) for i in pend])
    n = len(pend)
    if n:
        audit(conn, user, "notas_leidas", key or ",".join(map(str, ids or [])) or "todas", {"cantidad": n})
    return n


# ---------------------------------------------------------------- selección múltiple
def _plan(conn, user, keys: list, cambios: dict):
    cambios = normalizar({k: v for k, v in cambios.items() if k in CAMPOS_LOTE})
    unicas = list(dict.fromkeys(keys))
    existentes = {}
    for i in range(0, len(unicas), 500):
        parte = unicas[i:i + 500]
        for r in conn.execute(f"SELECT * FROM cases WHERE key_notif IN ({','.join('?' * len(parte))})", parte):
            existentes[r["key_notif"]] = dict(r)
    plan, omitidos = [], []
    for key in unicas:
        old = existentes.get(key)
        if old is None:
            omitidos.append({"key": key, "motivo": "no existe"})
            continue
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
    now = now_iso()
    cur = conn.execute("INSERT INTO lotes(at, user_id, username, cambios, casos) VALUES(?,?,?,?,?)",
                       (now, user["id"], user["username"], json.dumps({**cambios, "comentario": comentario or None}, ensure_ascii=False), "[]"))
    lid = cur.lastrowid
    hechos = []
    for p in plan:
        if p["diff"]:
            guardar(conn, user, p["key"], p["diff"], f"selección múltiple #{lid}")
            hechos.append({"key": p["key"], "diff": p["diff"]})
        if comentario:
            conn.defer("INSERT INTO comments(key_notif, user_id, username, at, text, visibilidad) VALUES(?,?,?,?,?,?)",
                       (p["key"], user["id"], user["username"], now, f"{comentario} (selección múltiple #{lid})", visibilidad))
    coment_ids = []
    if comentario and plan:
        coment_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM comments WHERE user_id=? AND at=? AND text=? ORDER BY id",
            (user["id"], now, f"{comentario} (selección múltiple #{lid})"))]
    conn.defer("UPDATE lotes SET casos=?, comentarios=? WHERE id=?",
               (json.dumps(hechos, ensure_ascii=False), json.dumps(coment_ids), lid))
    audit(conn, user, "lote", str(lid), {"casos": len(plan), "con_cambios": len(hechos), "comentarios": len(coment_ids),
                                         "omitidos": len(omitidos)})
    return {"id": lid, "con_cambios": len(hechos), "comentarios": len(coment_ids), "omitidos": omitidos}


def lotes_del_dia(conn, user) -> list:
    hoy = today().isoformat()
    filas = conn.execute("SELECT * FROM lotes WHERE LEFT(at, 10)=? ORDER BY id DESC", (hoy,))
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
    if r["at"][:10] != today().isoformat():
        raise GestionError("Solo se puede deshacer el mismo día.", 409)
    hechos = json.loads(r["casos"])
    actuales = {}
    claves = [h["key"] for h in hechos]
    for i in range(0, len(claves), 500):
        parte = claves[i:i + 500]
        for row in conn.execute(f"SELECT * FROM cases WHERE key_notif IN ({','.join('?' * len(parte))})", parte):
            actuales[row["key_notif"]] = dict(row)
    revertidos, saltados = 0, 0
    for h in hechos:
        cur = actuales.get(h["key"])
        if not cur:
            continue
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
    conn.defer("UPDATE lotes SET deshecho_at=?, deshecho_por=? WHERE id=?", (now_iso(), user["username"], lid))
    audit(conn, user, "lote_deshecho", str(lid), {"casos": revertidos, "campos_saltados": saltados})
    return {"casos": revertidos, "campos_saltados": saltados, "comentarios_quedan": len(ids)}
