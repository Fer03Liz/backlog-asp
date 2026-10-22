"""Foto de datos para enriquecer los casos, cargada una sola vez por petición (en un solo viaje a la base)."""
from collections import defaultdict
from datetime import timedelta

from ..domain.calendario import Calendar, iso, today
from ..domain.casos import enrich
from . import inventario

_CONSULTAS = [
    ("settings", "SELECT [key], value FROM settings"),
    ("festivos", "SELECT day FROM holidays_extra"),
    ("kgb", "SELECT * FROM kgb"),
    ("kgb_fecha", "SELECT TOP 1 file_date FROM loads WHERE kind='kgb' ORDER BY file_date DESC, id DESC"),
    ("prices", "SELECT * FROM prices"),
    ("parts", "SELECT * FROM parts"),
    ("arrivals", "SELECT tracking, arrived_on FROM guide_arrivals"),
    ("shipto", "SELECT * FROM shipto"),
    ("alias", "SELECT ship_to, ship_to_archivo FROM shipto_alias"),
    ("miami", "SELECT * FROM miami_purchases WHERE estado='Comprada' ORDER BY id"),
    ("oo_llegada", "SELECT part_norm, MIN(llegada_est) m FROM open_orders WHERE llegada_est IS NOT NULL GROUP BY part_norm"),
    ("inv_fecha", "SELECT MAX(file_date) m FROM loads WHERE kind='inventario'"),
    ("inv", "SELECT part_norm, SUM(cantidad) q FROM inventario GROUP BY part_norm"),
    ("inv_pendientes", """SELECT key_notif, assembly, option_jenny, eta_jenny, created_on, repair_strategy FROM cases
                          WHERE source_status='abierto' AND fecha_despacho IS NULL AND fecha_recepcion IS NULL"""),
    # Notas de la ACM que nadie del equipo ha marcado leídas
    ("notas", """SELECT m.key_notif, m.username, m.at, m.text FROM comments m
                 JOIN users u ON u.id = m.user_id AND u.role = 'acm'
                 LEFT JOIN notas_leidas l ON l.comment_id = m.id WHERE l.comment_id IS NULL ORDER BY m.id"""),
]


class Context:
    def __init__(self, conn):
        f = dict(zip((k for k, _ in _CONSULTAS), conn.select_many([(sql, ()) for _, sql in _CONSULTAS])))
        self.settings = {r["key"]: r["value"] for r in f["settings"]}
        self.today = today()
        self.cal = Calendar(r["day"] for r in f["festivos"])
        self.tol = int(self.settings.get("fr_tolerance_days", "2"))
        self.tol_business = self.settings.get("fr_tolerance_business", "0") == "1"
        self.grace = int(self.settings.get("despachado_grace_days", "3"))
        self.kam_days = int(self.settings.get("kam_alert_days", "2"))
        self.oh_max = int(self.settings.get("oh_max_days", "1"))
        self.exchange_codes = {c.strip().upper() for c in self.settings.get("exchange_codes", "OO").split(",") if c.strip()}
        self.currency = self.settings.get("currency", "USD")

        self.kgb_by_trk = defaultdict(list)
        self.kgb_by_part = {}
        for r in f["kgb"]:
            r = dict(r)
            if r["tracking"]:
                self.kgb_by_trk[r["tracking"]].append(r)
            p = self.kgb_by_part.setdefault(r["prod_norm"], {"qty": 0.0, "edd": None, "guides": set()})
            p["qty"] += r["qty"] or 0
            if r["edd"] and (p["edd"] is None or r["edd"] < p["edd"]):
                p["edd"] = r["edd"]
            if r["tracking"]:
                p["guides"].add(r["tracking"])
        self.kgb_date = f["kgb_fecha"][0]["file_date"] if f["kgb_fecha"] else None

        self.prices, self.prices_norm = {}, {}
        for r in f["prices"]:
            self.prices[r["part"]] = dict(r)
            self.prices_norm.setdefault(r["part_norm"], dict(r))
        self.parts = {r["part"]: dict(r) for r in f["parts"]}
        self.parts_norm = {}
        for p in self.parts.values():
            self.parts_norm.setdefault(p["part_norm"], p)
        self.arrivals = {r["tracking"]: r["arrived_on"] for r in f["arrivals"]}

        # Tiendas: las del backlog asociadas por nombre toman los datos de la tienda de Ingram
        self.shipto = {r["ship_to"]: dict(r) for r in f["shipto"]}
        for r in f["alias"]:
            if r["ship_to"] not in self.shipto and r["ship_to_archivo"] in self.shipto:
                self.shipto[r["ship_to"]] = {**self.shipto[r["ship_to_archivo"]], "ship_to": r["ship_to"],
                                             "asociada_a": r["ship_to_archivo"]}

        # Compras en Miami activas por caso y llegada estimada de Open Orders por parte
        self.miami_lead = int(self.settings.get("miami_lead_days", "7"))
        self.miami = {r["key_notif"]: dict(r) for r in f["miami"]}
        self.oo_llegada = {r["part_norm"]: r["m"] for r in f["oo_llegada"]}

        # Inventario propio repartido entre los casos pendientes
        self.inv_fecha = f["inv_fecha"][0]["m"] if f["inv_fecha"] else None
        self.inv_por_parte, self.inv_asignado = {}, {}
        if self.inv_fecha:
            self.inv_por_parte = {r["part_norm"]: r["q"] for r in f["inv"]}
            self.inv_asignado = inventario.asignar(self, [dict(r) for r in f["inv_pendientes"]])

        self.notas_acm = {}
        for r in f["notas"]:
            n = self.notas_acm.setdefault(r["key_notif"], {"n": 0})
            n.update(n=n["n"] + 1, username=r["username"], at=r["at"], text=r["text"])


def load_cases(conn, ctx: Context, include_closed: bool = False, closed_days: int = 45) -> list:
    if include_closed:
        since = iso(ctx.today - timedelta(days=closed_days))
        rows = conn.execute("SELECT * FROM cases WHERE source_status='abierto' OR closed_on >= ?", (since,))
    else:
        rows = conn.execute("SELECT * FROM cases WHERE source_status='abierto'")
    out = [enrich(dict(r), ctx) for r in rows]
    out.sort(key=lambda x: x["prioridad"])
    return out
