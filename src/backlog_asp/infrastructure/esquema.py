"""Esquema de la base en SQL Server. Se crea solo al arrancar (tablas e índices que falten); nunca borra datos.

Tipos: las fechas se guardan como texto ISO (AAAA-MM-DD o AAAA-MM-DDTHH:MM:SS), igual que en la versión SQLite,
para que las comparaciones y el JSON de la API no cambien. La bitácora y los comentarios no se pueden editar ni
borrar (triggers INSTEAD OF).
"""
from .. import config
from ..domain.catalogos import DEFAULT_SETTINGS

K = "NVARCHAR(100)"      # llaves y columnas con índice
T = "NVARCHAR(400)"      # texto corto
TX = "NVARCHAR(MAX)"     # texto libre y JSON
D = "NVARCHAR(30)"       # fechas en texto ISO
N = "FLOAT"

TABLAS = {
    "users": f"""id INT IDENTITY(1,1) PRIMARY KEY, username NVARCHAR(64) NOT NULL UNIQUE, full_name {T},
        pw_hash NVARCHAR(200) NOT NULL,
        role NVARCHAR(10) NOT NULL CHECK(role IN ('admin','editor','lector','acm','tienda')),
        active INT NOT NULL DEFAULT 1, failed_attempts INT NOT NULL DEFAULT 0, locked_until {D},
        must_change_pw INT NOT NULL DEFAULT 0, created_at {D} NOT NULL""",
    "sessions": f"""token_hash NVARCHAR(64) PRIMARY KEY, user_id INT NOT NULL REFERENCES [{{s}}].[users](id),
        csrf NVARCHAR(64) NOT NULL, created_at {D} NOT NULL, last_seen {D} NOT NULL, expires_at {D} NOT NULL""",
    "settings": "[key] NVARCHAR(64) PRIMARY KEY, value NVARCHAR(2000) NOT NULL",
    "loads": f"""id INT IDENTITY(1,1) PRIMARY KEY, kind NVARCHAR(20) NOT NULL, file_name {T}, file_hash {K} NOT NULL,
        file_date {D}, loaded_at {D} NOT NULL, user_id INT, [rows] INT, summary {TX}, CONSTRAINT uq_loads UNIQUE(kind, file_hash)""",
    "cases": f"""key_notif {K} PRIMARY KEY,
        notifctn {T}, line_item INT, assembly {T}, description {T}, created_on {D}, po_number {T}, ship_to {K},
        list_name {T}, material_desc {T}, serial_no {T}, warranty {T}, report_date {D}, product_family {T},
        repair_strategy {T}, product_description {T},
        option_jenny {T}, eta_jenny {D}, eta_first {D}, eta_changes INT NOT NULL DEFAULT 0,
        comments_jenny {TX}, notes_jenny {TX}, tracking {TX}, subs {TX},
        first_seen {D}, last_seen {D}, source_status NVARCHAR(20) NOT NULL DEFAULT 'abierto',
        closed_on {D}, close_reason {T}, reopened INT NOT NULL DEFAULT 0, oh_since {D},
        estado NVARCHAR(60) NOT NULL DEFAULT 'Nuevo', responsable {T},
        fecha_recepcion {D}, fecha_despacho {D}, fecha_objetivo {D}, fecha_comunicada {D},
        propuesta {T}, aprobacion_kam NVARCHAR(30) NOT NULL DEFAULT 'No requiere',
        kam_solicitado {D}, kam_respuesta {D}, updated_at {D}""",
    "case_history": f"""id INT IDENTITY(1,1) PRIMARY KEY, key_notif {K} NOT NULL, load_id INT, file_date {D},
        field {T} NOT NULL, old {TX}, new {TX}""",
    "snapshots": f"""file_date {D} NOT NULL, key_notif {K} NOT NULL, option_jenny {T}, eta_jenny {D},
        PRIMARY KEY(file_date, key_notif)""",
    "comments": f"""id INT IDENTITY(1,1) PRIMARY KEY, key_notif {K} NOT NULL, user_id INT, username NVARCHAR(64),
        at {D} NOT NULL, text {TX} NOT NULL, visibilidad NVARCHAR(20) NOT NULL DEFAULT 'interno'""",
    "audit_log": f"""id INT IDENTITY(1,1) PRIMARY KEY, at {D} NOT NULL, user_id INT, username NVARCHAR(64),
        action NVARCHAR(60) NOT NULL, target {T}, detail {TX}""",
    "kgb": f"""tracking {K}, delivery_id {T}, delivery_item {T}, poso {K}, poso_item {T}, prod_id {T}, prod_norm {K},
        prod_desc {T}, status {T}, current_event {T}, event_dt {D}, edd {D}, pgi_dt {D}, sla_ind {T}, issue {TX},
        qty {N}, cust_id {T}, ship_country {T}""",
    "prices": f"""part {K} PRIMARY KEY, part_norm {K}, description {T}, stocking {N}, exchange {N}, updated_at {D},
        battery {N}, display {N}, display_backglass {N}, backglass {N}, addon {N}""",
    "parts": f"""part {K} PRIMARY KEY, part_norm {K}, description {T}, product {T}, component_group {T},
        substitute {T}, updated_at {D}""",
    "shipto": f"""ship_to {K} PRIMARY KEY, name {T}, email NVARCHAR(1000), city {T}, updated_at {D},
        cc_email NVARCHAR(1000), cliente {T}""",
    "guide_arrivals": f"tracking {K} PRIMARY KEY, arrived_on {D} NOT NULL, user_id INT, at {D} NOT NULL",
    "holidays_extra": f"day {D} PRIMARY KEY, name {T}",
    "emails_log": f"id INT IDENTITY(1,1) PRIMARY KEY, at {D} NOT NULL, user_id INT, ship_to {K}, n_cases INT, keys {TX}",
    "open_orders": f"""sales_ord {K} NOT NULL, item {K} NOT NULL, file_date {D} NOT NULL,
        cust_num {T}, ref_web {T}, cust_po {T}, tipo {T}, plant {T}, entry_date {D}, req_date {D},
        part {T}, part_norm {K}, description {T}, ord_qty {N}, remaining_qty {N}, scheduled_qty {N},
        status {T}, bloqueo {T}, recovery_raw {TX}, recovery_date {D}, salida_est {D}, llegada_est {D},
        first_seen {D}, PRIMARY KEY(sales_ord, item)""",
    "open_order_history": f"""file_date {D} NOT NULL, sales_ord {K} NOT NULL, item {K} NOT NULL, part {T},
        part_norm {K}, remaining_qty {N}, status {T}, recovery_date {D}, salida_est {D},
        PRIMARY KEY(file_date, sales_ord, item)""",
    "inventario": f"part {T}, part_norm {K}, cantidad {N}, bodega {T}, descripcion {T}, file_date {D}",
    "inventario_history": f"file_date {D} NOT NULL, part_norm {K} NOT NULL, cantidad {N}, PRIMARY KEY(file_date, part_norm)",
    "miami_purchases": f"""id INT IDENTITY(1,1) PRIMARY KEY, key_notif {K} NOT NULL, part {T}, part_norm {K},
        cantidad {N} NOT NULL DEFAULT 1, fecha_compra {D} NOT NULL, eta {D}, po {T}, notas {TX},
        estado NVARCHAR(20) NOT NULL DEFAULT 'Comprada' CHECK(estado IN ('Comprada','Recibida','Cancelada')),
        fecha_estado {D}, motivo {TX}, oo_sales_ord {T}, oo_item {T}, origen NVARCHAR(40) NOT NULL DEFAULT 'manual',
        user_id INT, username NVARCHAR(64), created_at {D} NOT NULL, updated_at {D}""",
    "shipto_alias": f"""ship_to {K} PRIMARY KEY, ship_to_archivo {K} NOT NULL, origen NVARCHAR(60) NOT NULL,
        username NVARCHAR(64), at {D} NOT NULL""",
    "user_shipto": f"""user_id INT NOT NULL REFERENCES [{{s}}].[users](id), ship_to {K} NOT NULL,
        PRIMARY KEY(user_id, ship_to)""",
    "solicitudes_fecha": f"""id INT IDENTITY(1,1) PRIMARY KEY, key_notif {K} NOT NULL,
        campo NVARCHAR(30) NOT NULL CHECK(campo IN ('fecha_recepcion','fecha_despacho')),
        valor_actual {D}, valor_nuevo {D}, motivo {TX} NOT NULL,
        estado NVARCHAR(20) NOT NULL DEFAULT 'Pendiente' CHECK(estado IN ('Pendiente','Aprobada','Rechazada')),
        solicitado_por NVARCHAR(64), solicitado_at {D} NOT NULL, resuelto_por NVARCHAR(64), resuelto_at {D}, respuesta {TX}""",
    "notas_leidas": f"comment_id INT PRIMARY KEY, username NVARCHAR(64), at {D} NOT NULL",
    "lotes": f"""id INT IDENTITY(1,1) PRIMARY KEY, at {D} NOT NULL, user_id INT, username NVARCHAR(64),
        cambios {TX} NOT NULL, casos {TX} NOT NULL, comentarios {TX}, deshecho_at {D}, deshecho_por NVARCHAR(64)""",
}
# Orden de creación (las llaves foráneas van después de users) y de borrado inverso.
ORDEN = list(TABLAS)

INDICES = [
    ("idx_cases_ship", "cases", "ship_to"), ("idx_cases_status", "cases", "source_status"),
    ("idx_hist_key", "case_history", "key_notif"), ("idx_comments_key", "comments", "key_notif"),
    ("idx_audit_at", "audit_log", "at"), ("idx_audit_target", "audit_log", "target, action"),
    ("idx_kgb_trk", "kgb", "tracking"), ("idx_kgb_prod", "kgb", "prod_norm"), ("idx_kgb_poso", "kgb", "poso"),
    ("idx_prices_norm", "prices", "part_norm"), ("idx_parts_norm", "parts", "part_norm"),
    ("idx_oo_part", "open_orders", "part_norm"), ("idx_ooh_part", "open_order_history", "part_norm"),
    ("idx_inv_part", "inventario", "part_norm"), ("idx_miami_key", "miami_purchases", "key_notif"),
    ("idx_miami_part", "miami_purchases", "part_norm"), ("idx_solicitudes_key", "solicitudes_fecha", "key_notif"),
    ("idx_sessions_user", "sessions", "user_id"), ("idx_loads_kind", "loads", "kind, file_date"),
]

# Columnas agregadas después de crear la base: (tabla, columna, tipo). Se agregan solas al arrancar.
MIGRACIONES: list = []

SOLO_LECTURA = [
    ("audit_log", "La bitácora es de solo lectura"),
    ("comments", "Los comentarios no se editan ni se borran"),
]


def _ddl(s: str) -> list:
    out = [f"IF SCHEMA_ID(N'{s}') IS NULL EXEC(N'CREATE SCHEMA [{s}]')"]
    for name in ORDEN:
        cols = TABLAS[name].replace("{s}", s)
        out.append(f"IF OBJECT_ID(N'[{s}].[{name}]', N'U') IS NULL CREATE TABLE [{s}].[{name}]({cols})")
    for idx, table, cols in INDICES:
        out.append(f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'{idx}' AND object_id=OBJECT_ID(N'[{s}].[{table}]')) "
                   f"CREATE INDEX [{idx}] ON [{s}].[{table}]({cols})")
    for table, column, kind in MIGRACIONES:
        out.append(f"IF COL_LENGTH(N'[{s}].[{table}]', N'{column}') IS NULL ALTER TABLE [{s}].[{table}] ADD [{column}] {kind}")
    return out


def _triggers(s: str) -> list:
    # CREATE OR ALTER TRIGGER debe ir solo en su lote.
    return [f"CREATE OR ALTER TRIGGER [{s}].[{table}_solo_lectura] ON [{s}].[{table}] INSTEAD OF UPDATE, DELETE AS "
            f"BEGIN SET NOCOUNT ON; THROW 50001, N'{msg}', 1; END" for table, msg in SOLO_LECTURA]


def init_db(conn) -> None:
    """Crea el esquema, las tablas, índices y triggers que falten, y los parámetros por defecto."""
    s = config.DB_SCHEMA
    for sql in _ddl(s):
        conn.execute(sql)
    conn.commit()
    for sql in _triggers(s):
        conn.execute(sql)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("IF NOT EXISTS (SELECT 1 FROM settings WHERE [key]=?) INSERT INTO settings([key], value) VALUES(?, ?)",
                     (k, k, v))
    conn.commit()


def drop_all(conn) -> None:
    """Borra el esquema completo. Solo para pruebas: se niega con cualquier esquema que no sea de pruebas."""
    s = config.DB_SCHEMA
    if not s.startswith("backlog_test"):
        raise RuntimeError(f"drop_all solo se permite en esquemas de prueba (backlog_test*), no en '{s}'.")
    for table, _ in SOLO_LECTURA:
        conn.execute(f"IF OBJECT_ID(N'[{s}].[{table}_solo_lectura]', N'TR') IS NOT NULL DROP TRIGGER [{s}].[{table}_solo_lectura]")
    for name in reversed(ORDEN):
        conn.execute(f"IF OBJECT_ID(N'[{s}].[{name}]', N'U') IS NOT NULL DROP TABLE [{s}].[{name}]")
    conn.execute(f"IF SCHEMA_ID(N'{s}') IS NOT NULL EXEC(N'DROP SCHEMA [{s}]')")
    conn.commit()
