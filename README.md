# Tablero de backlog ASP

Aplicación web interna para el seguimiento diario del backlog de repuestos de Apple (Apple Spare Parts)
en Ingram Micro Colombia.

Cada día se cargan los reportes de Apple y el tablero hace cuatro cosas:

1. **Cruza la información.** Une el backlog de casos con el tránsito (KGB) y con los pedidos pendientes (Open Orders).
2. **Calcula fechas.** Para cada caso calcula cuándo llega el repuesto, cuándo se puede despachar y si se cumple la ETA.
3. **Marca alertas.** Ordena los casos por prioridad y señala los que están en riesgo.
4. **Registra la gestión.** Guarda lo que hace el equipo: recepción, despacho, comentarios, solicitudes y aprobaciones.

Es una sola aplicación con su base de datos en un archivo. No usa servicios en la nube ni se conecta a SAP
ni a Apple: los archivos se cargan a mano.

---

## 1. Qué hace el programa

### Flujo diario

| Paso | Quién | Qué pasa en el tablero |
|---|---|---|
| 1. Cargar el **backlog** del día | Planeación | Crea los casos nuevos y actualiza solución, ETA, Comments y Notes de los que siguen (cada cambio queda en el historial). Cierra los que ya no aparecen y reabre los que vuelven. Nunca borra la gestión del equipo. |
| 2. Cargar el **KGB** y el **Open Orders** | Planeación | Reemplaza el tránsito y los pedidos del día anterior y guarda la foto diaria. Cruza la guía de cada caso con el KGB. |
| 3. Revisar **Casos** | Equipo y ACM | Lista ordenada por prioridad (OH, rojos, cercanía al plazo), con filtros, carril de fechas y detalle de cada caso. |
| 4. Registrar **recepción y despacho** | Torre de control | Por caso, por guía (llegada de un envío completo) o en bloque (selección múltiple). |
| 5. Enviar **correos** a las tiendas | Planeación | Un correo por tienda con sus casos por despachar, en gestión y despachados. |

### Pantallas

| Pantalla | Para qué sirve |
|---|---|
| **Casos** | Todas las líneas del backlog con filtros (solución, tienda, estado, alertas, Super ASP), agrupación y orden. El detalle de cada caso muestra alertas, fechas calculadas, gestión, tránsito (KGB), pedido en Apple (Open Orders), datos de Apple, comentarios o notas, e historial. Permite selección múltiple para aplicar cambios a varios casos. |
| **Guías** | Casos agrupados por número de guía, con su estado en el KGB. Al registrar la llegada de una guía, todos sus casos quedan recibidos. |
| **Abastecimiento** | Por parte: demanda del backlog frente a lo que viene en el KGB, lo pedido en Open Orders, el inventario propio y las compras en Miami. Señala las partes que nadie está trayendo. |
| **Correos** | Correo diario por tienda (texto para copiar o borrador `.eml` para Outlook). Identifica cada línea por notificación y número de parte; nunca incluye precios, PO ni datos de otras tiendas. |
| **Indicadores** | Aging promedio del backlog y de despacho, fill rate, líneas en riesgo, OH por despachar, espera de la ACM, flujo diario, tiendas y porcentaje del KGB que va a Colombia. |
| **Cargas** | Subir los archivos y ver el historial de cargas. Hay plantillas para inventario, precios y tiendas. |
| **Administración** | Usuarios y roles, tiendas de cada usuario tienda, correos de las tiendas, solicitudes de cambio de fecha, parámetros, días no hábiles y bitácora. |
| **Mis casos** | Única pantalla del rol tienda: el estado y la fecha de sus propios casos. |

### Avisos

- **Campana "Avisos"** (equipo interno). Cuenta las notas nuevas de la ACM. Cada nota queda sin leer, y con una alerta en el caso, hasta que alguien la marca leída.
- **Solicitudes pendientes.** El administrador ve en la misma campana las solicitudes de cambio de fecha que esperan su respuesta.

---

## 2. Roles y accesos

El control está en el servidor. Cada petición se valida contra lo que su rol puede hacer, y la pantalla solo
muestra lo permitido.

| Acción | Administrador | Editor (torre de control) | Lector (jefatura) | Apple Care Manager | Tienda |
|---|:-:|:-:|:-:|:-:|:-:|
| Ver casos, guías, indicadores | Sí | Sí | Sí | Casos, Abastecimiento e Indicadores | Solo sus casos |
| Ver precios y valor del backlog | Sí | Sí | Sí | No | No |
| Ver compras en Miami e inventario propio | Sí | Sí | Sí | No (ve "Estimada por Ingram") | No |
| Cambiar gestión del caso (estado, responsable, propuesta, aprobación) | Sí | Sí | No | No | No |
| Registrar recepción y despacho | Sí, y puede corregirlas | Una sola vez; para cambiar, solicitud | No | No | No |
| Aprobar o rechazar solicitudes de cambio de fecha | Sí | No | No | No | No |
| Selección múltiple (cambios en bloque) | Sí | Sí | No | No | No |
| Comentarios del equipo | Escribe y lee todos | Escribe y lee todos | Lee todos | Lee solo los "Compartido con Apple" | No |
| Notas para el equipo | — | — | — | Escribe (generan aviso) | No |
| Marcar notas de la ACM como leídas | Sí | Sí | Sí | No | No |
| Correos a tiendas | Sí | Sí | Solo ver | No | No |
| Cargar archivos | Sí | Sí | Solo ver el historial | No | No |
| Exportar CSV | Sí | Sí | Sí | No | No |
| Usuarios, parámetros, festivos, bitácora, correos de tiendas | Sí | No | No | No | No |

Los usuarios internos son el administrador (Planeación), los editores (torre de control) y los lectores
(jefatura, solo consulta). Detalle de los roles externos:

- **Apple Care Manager (ACM)**
  - **Qué ve:** todos los casos y sus fechas y alertas operativas.
  - **Qué no ve:** precios, compras en Miami, inventario propio, guías, correos, cargas, administración ni comentarios internos.
  - **Gestión:** en el historial solo ve estado, propuesta, aprobación y fechas.
  - **Qué hace:** deja notas en los casos; no edita nada más.
- **Tienda**
  - **Qué ve:** solo los casos de los ship-to que el administrador le asigna, con cuatro estados:
    - en bodega por despachar, con despacho estimado;
    - en proceso, con fecha tentativa igual a la ETA de Apple;
    - en gestión, sin fecha;
    - despachado, durante los últimos 15 días.
  - **Qué no ve:** PO, precios, comentarios ni datos de otras tiendas. No escribe nada.

---

## 3. Base de datos

### Motor y ubicación

- **Motor:** SQLite en modo WAL, en un solo archivo: `data/backlog.db`. Es SQL estándar y no necesita un servidor de base de datos aparte.
- **Arranque:** al iniciar, la app crea las tablas que falten y agrega las columnas nuevas (migraciones automáticas). Nunca borra datos.
- **Confidencialidad:** `data/` contiene información confidencial y está excluida del repositorio (`.gitignore`), igual que cualquier `.db`, `.xlsx`, `.csv` o `.eml`.

### Tablas (26)

| Grupo | Tablas | Cómo se escriben |
|---|---|---|
| Casos | `cases`, `case_history`, `snapshots` | La carga del backlog inserta y actualiza los casos; los cambios de Apple quedan en `case_history` y la foto diaria en `snapshots`. Los casos nunca se borran: se cierran. |
| Gestión del equipo | `comments`, `notas_leidas`, `solicitudes_fecha`, `lotes`, `guide_arrivals`, `emails_log` | Las escriben los usuarios desde la app. Los comentarios no se pueden editar ni borrar (triggers). |
| Datos de Apple | `kgb`, `open_orders`, `open_order_history` | Cada carga reemplaza el KGB y el Open Orders vigentes; el historial de Open Orders acumula una foto por día. |
| Abastecimiento propio | `inventario`, `inventario_history`, `miami_purchases` | El inventario se reemplaza en cada carga y guarda su foto diaria. Las compras en Miami se registran a mano. |
| Maestros | `prices`, `parts`, `shipto`, `shipto_alias`, `holidays_extra`, `settings` | Precios y Parts Data se reemplazan completos en cada carga. Las tiendas se actualizan una por una. Lo demás se edita en Administración. |
| Acceso y control | `users`, `sessions`, `user_shipto`, `loads`, `audit_log` | `audit_log` es la bitácora: no se puede editar ni borrar (triggers). `loads` registra cada archivo cargado. |

### Reglas de escritura

- **Archivos repetidos o viejos:** se rechazan un archivo ya cargado y uno con fecha anterior al último cargado del mismo tipo.
- **Atomicidad:** cada carga corre en una transacción; si algo falla, la base queda como estaba.
- **Bitácora:** cada cambio de gestión registra el antes y el después.
- **Fechas protegidas:** la recepción y el despacho registrados por un editor solo cambian con una solicitud aprobada por el administrador.
- **Tandas en bloque:** se pueden deshacer el mismo día. Si un campo cambió después, no se toca, y los comentarios quedan.

### Respaldos

- **Automáticos:** antes de cada carga de backlog, KGB y Open Orders se guarda una copia consistente en `data/backups/`, y se conservan las 30 últimas.
- **Restaurar:** basta con cerrar la app y reemplazar `data/backlog.db` por la copia elegida.

### Uso desde otras herramientas

- **Power BI:** se puede leer en modo lectura con un controlador ODBC de SQLite. También se puede usar la exportación CSV de la pantalla Casos.
- **Migrar a SQL Server o PostgreSQL:** es posible. Se ajustan unas 30 consultas propias de SQLite (insertar o reemplazar, lectura de estructura, triggers y migraciones) y se copian los datos.

---

## 4. Reglas de negocio

**Cruce con el KGB.**
- **Guía:** la que la ACM escribe en *Comments* se busca en la columna `TRACKING_NR` del KGB.
- **Parte:** dentro de la guía se busca la parte sin prefijo regional, o un sustituto oficial o de los comentarios.
- **Notes:** la guía que aparece en *Notes* es la retrasada original y no se usa.

**Fechas del caso.**
- **Llegada:** la primera que exista, en este orden: recepción registrada, OH, EDD de la guía en el KGB y ETA de Apple.
- **Despacho posible:** el día hábil siguiente a la llegada. Nunca el mismo día, ni fines de semana ni festivos de Colombia, que se calculan solos (Ley Emiliani y Semana Santa).
- **Plazo de alertas:** ETA + 2 días (parámetro). Pasado ese día el caso queda en rojo.

**Indicadores.**
- **Aging del backlog:** días promedio desde la creación de las líneas por despachar.
- **Aging de despacho:** días promedio de la creación al despacho, en lo despachado los últimos 30 días.
- **Fill rate:** líneas cuya primera ETA de Apple ya pasó; cuentan a tiempo si se despacharon en esa ETA o antes. La primera ETA no se mueve aunque Apple la corra.

**Prioridad.** OH primero, luego rojos, luego ámbar y, dentro de cada grupo, lo más cercano al plazo.
Super ASP (estrategia Z8.2) desempata.

**Alertas principales.**

| Alerta | Nivel | Cuándo |
|---|---|---|
| Venció el plazo | Rojo | Pasó la ETA + 2 días y no hay despacho |
| Despacho después del límite | Rojo | El despacho posible cae después del plazo |
| OH demorado | Rojo | OH sin despachar por más de 1 día hábil |
| Sin margen | Ámbar | El despacho posible es el último día del plazo |
| ETA antes de la llegada KGB | Ámbar | La ETA de Apple es anterior a la EDD de la guía |
| Guía no aparece / sin la parte | Ámbar | La guía de Comments no está en el KGB o no trae la parte |
| Guía retrasada | Ámbar | La EDD pasa la ETA o Apple reporta demora (Shipment Delay) |
| Llegada sin recepción | Ámbar | Ya debió llegar y nadie registró la recepción |
| Esperando a la ACM | Ámbar | Aprobación de la ACM pendiente por más de 2 días |
| Nota nueva de la ACM | Ámbar | La ACM dejó una nota que nadie ha marcado leída |
| Se puede despachar con inventario propio | Ámbar | El inventario cargado tiene unidad libre para el caso |
| Fuera de SLA Apple | Info | Apple marca la guía fuera de su SLA, pero llega antes de la ETA |
| ETA en día no hábil | Info | La ETA cae en fin de semana o festivo |

---

## 5. Seguridad

- **Red:** solo escucha en `127.0.0.1`, así que nadie más en la red puede abrirla. Para usarla en equipo hay que publicarla con TI (sección 6).
- **Contraseñas:** se guardan con hash scrypt y sal aleatoria. Mínimo 12 caracteres, con letras y números. Las contraseñas temporales obligan a cambiarlas al ingresar.
- **Bloqueo:** 15 minutos después de 5 intentos fallidos.
- **Sesiones:** cookie HttpOnly y SameSite=Strict. En la base solo queda el hash del token. Se cierran tras 60 minutos de inactividad y duran como máximo 8 horas.
- **Formularios:** token CSRF en todas las escrituras.
- **Navegador:** política de seguridad de contenido (CSP) estricta, sin scripts externos ni en línea. La interfaz nunca inserta datos como HTML.
- **Archivos:** hasta 25 MB, solo `.xlsx` o `.csv`, leídos en memoria con protección contra XML malicioso.
- **Exportación:** el CSV exportado neutraliza fórmulas para que Excel no las ejecute.
- **Bitácora:** de solo lectura. Registra ingresos, cargas, cambios, correos, solicitudes, aprobaciones y administración.

---

## 6. Instalación y operación

**En un equipo (Windows).** Requiere Python 3.10 o superior.

1. Copiar la carpeta a un lugar fijo, por ejemplo `C:\Herramientas\Backlog\backlog-asp`.
2. Doble clic en `iniciar.bat`. La primera vez crea el entorno, instala dependencias y pide crear el
   usuario administrador.
3. Abrir http://127.0.0.1:8765.

Equivalente en consola:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts\crear_admin.py
python run.py
```

**En un servidor (para que la use el equipo).** Lo hace TI:

1. Instalar en un servidor o máquina virtual interna, como servicio, con Python y `requirements.txt`. 1–2 vCPU y 2 GB de RAM son suficientes.
2. Publicarla detrás de un proxy inverso con HTTPS (IIS, nginx u otro) y definir `BACKLOG_COOKIE_SECURE=1`.
   El proxy es lo único que llega a `127.0.0.1:8765`.
3. Respaldar `data/` según la política de TI.

No cambie `HOST` en `app/config.py` para abrirla a la red sin HTTPS: las contraseñas viajarían en claro.

**Archivos que se cargan.**

| Archivo | Frecuencia | Efecto |
|---|---|---|
| Backlog de la ACM (hojas Backlog y Allocated) | Diario | Crea, actualiza, cierra y reabre casos |
| KGB Open Order | Diario | Reemplaza el tránsito; se filtra al cliente configurado |
| Colombia Open Orders | Diario | Reemplaza los pedidos pendientes y guarda la foto del día |
| Inventario propio | Cuando se tenga | Reemplaza el inventario; reconoce columnas como Parte/Material y Cantidad/On hand |
| Precios | Ocasional | Reemplaza todos los precios |
| Parts Data de Apple | Ocasional | Reemplaza descripciones y sustitutos oficiales |
| Tiendas (ship-to) | Ocasional | Actualiza nombre, ciudad, correo y copia de cada tienda |

---

## 7. Parámetros (Administración)

| Parámetro | Por defecto | Nota |
|---|---|---|
| Cliente KGB | código de cliente actual | Varios separados por coma |
| Tolerancia del plazo | 2 | Días después de la ETA para alertas |
| Tolerancia en días hábiles | 0 | 0 = calendario, 1 = hábiles |
| Garantías exchange | OO | Garantías que se pagan a precio exchange |
| Días de espera de la ACM | 2 | Antes de la alerta "Esperando a la ACM" |
| Días máximos de OH | 1 | Hábiles |
| Tránsito de Open Orders | 22 | Días de Apple a Colombia después del Recovery ETA |
| Lead time de Miami | 7 | Días desde la compra hasta la llegada |

---

## 8. Estructura del código

```
app/
  main.py            rutas HTTP, cabeceras de seguridad, cargas, correos, exportación
  security.py        contraseñas, sesiones, bloqueo
  acceso.py          roles externos (ACM y tienda) y filtros de datos
  logic.py           fechas, cruce con el KGB, alertas, prioridad e indicadores
  ingest.py          carga de backlog, precios, Parts Data y tiendas
  abastecimiento.py  carga de KGB y Open Orders, abastecimiento por parte
  inventario.py      carga del inventario propio y asignación a casos
  miami.py           compras en Miami
  tiendas.py         asociación de tiendas del backlog con el archivo de tiendas
  gestion.py         cambios de casos, fechas protegidas, solicitudes, avisos y selección múltiple
  db.py              esquema, migraciones, bitácora y respaldos
  config.py          parámetros y constantes
static/              interfaz: HTML, CSS y JavaScript sin librerías externas
scripts/             crear_admin.py
tests/               prueba de punta a punta
data/                se crea al iniciar: base y respaldos (no se versiona)
```

Dependencias (`requirements.txt`): FastAPI, Uvicorn, pandas, openpyxl, defusedxml y python-multipart.

## 9. Pruebas

`tests/test_flujo.py` recorre el flujo completo con un backlog y un KGB reales, en una base temporal que no toca
`data/` (requiere `pip install httpx`). Valida login y CSRF, cargas y rechazo de archivos repetidos o viejos,
cruce con el KGB, gestión, guías, correos, roles (lector, editor, ACM y tienda), fechas protegidas con
solicitud al administrador, bloqueo por intentos fallidos y bitácora de solo lectura:

```
python tests/test_flujo.py <backlog.xlsx> <kgb.xlsx>
```

La variable `BACKLOG_HOY` fija la fecha de "hoy" y existe solo para pruebas.
