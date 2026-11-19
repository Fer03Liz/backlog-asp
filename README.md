# Tablero de backlog ASP

Aplicación web interna para el seguimiento diario del backlog de repuestos de Apple (Apple Spare Parts)
en Ingram Micro Colombia.

Cada día se cargan los reportes de Apple y el tablero hace cuatro cosas:

1. **Cruza la información.** Une el backlog de casos con el tránsito (KGB) y con los pedidos pendientes (Open Orders).
2. **Calcula fechas.** Para cada caso calcula cuándo llega el repuesto, cuándo se puede despachar y si se cumple la ETA.
3. **Marca alertas.** Ordena los casos por prioridad y señala los que están en riesgo.
4. **Registra la gestión.** Guarda lo que hace el equipo: recepción, despacho, comentarios, solicitudes y aprobaciones.

Es una sola aplicación con su base de datos en SQL Server (esquema propio `backlog`). No usa servicios en la
nube ni se conecta a SAP ni a Apple: los archivos se cargan a mano.

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
| **Correos** | Correo diario por tienda (texto para copiar o borrador `.eml` para Outlook). Identifica cada línea por notificación y número de parte; nunca incluye precios, PO ni datos de otras tiendas. Tiene buscador por tienda, ship-to o correo, y el panel para asociar tiendas con el archivo de tiendas. |
| **Indicadores** | Aging promedio del backlog y de despacho, fill rate, líneas en riesgo, OH por despachar, espera de la ACM, flujo diario, tiendas y porcentaje del KGB que va a Colombia. |
| **Cargas** | Subir los archivos y ver el historial de cargas. Hay plantillas para inventario, precios y tiendas. |
| **Solicitudes** | Solo supervisor y administrador: aprobar o rechazar los cambios de fechas de recepción y despacho. |
| **Administración** | Solo administrador: usuarios y roles, parámetros, días no hábiles y bitácora. |

### Estados del caso

`Nuevo` → `En gestión` → `En cotización` → `En autorización` → `En tránsito por Miami` → `Recibido` → `Despachado` → `Entregado` (más `Sin solución`).
Registrar la recepción lleva a `Recibido` los casos en `Nuevo`, `En gestión` o `En tránsito por Miami`. Marcar `Despachado` o `Entregado`
sin fecha de despacho registra la de hoy. El campo **Novedad en despacho** (texto libre, 500 caracteres) guarda los inconvenientes del despacho;
el caso muestra la etiqueta «Novedad» en la lista. La «Fecha a comunicar» se eliminó: el estado de cada caso se comunica a diario por correo.

### Avisos

- **Campana "Avisos"** (equipo interno). Muestra las solicitudes de cambio de fecha pendientes (para supervisor y administrador) y las respuestas recientes a las propias.
- **Solicitudes pendientes.** El administrador ve en la misma campana las solicitudes de cambio de fecha que esperan su respuesta.

---

## 2. Roles y accesos

El control está en el servidor. Cada petición se valida contra lo que su rol puede hacer, y la pantalla solo
muestra lo permitido.

| Acción | Administrador | Supervisor | Comprador | Atención al cliente |
|---|:-:|:-:|:-:|:-:|
| Ver casos, guías, abastecimiento, indicadores, precios, Miami e inventario | Sí | Sí | Sí | Sí |
| Cambiar gestión del caso (estado, responsable, novedad, propuesta, aprobación) | Sí | Sí | Sí | No |
| Registrar recepción y despacho | Sí, y puede corregirlas | Sí, y puede corregirlas | Una sola vez; para cambiar, solicitud | No |
| Aprobar o rechazar solicitudes de cambio de fecha (pestaña Solicitudes) | Sí | Sí | No | No |
| Selección múltiple (cambios en bloque) y deshacer la tanda | Sí | Sí (también las ajenas) | Sí (solo las propias) | No |
| Registrar llegada de guías | Sí | Sí | Sí | No |
| Compras en Miami | Sí | Sí | Sí | No |
| Comentar casos | Sí | Sí | Sí | Sí |
| Correos a tiendas, correo de cada tienda y asociación de tiendas | Sí | Sí | No | Sí |
| Cargar backlog e inventario | Sí | Sí | No | No |
| Cargar KGB y Open Orders | Sí | Sí | Sí | No |
| Cargar precios y Parts Data | Sí | No | Sí | No |
| Cargar archivo de tiendas | Sí | Sí | No | Sí |
| Exportar CSV | Sí | Sí | Sí | Sí |
| Usuarios, parámetros, festivos, bitácora | Sí | No | No | No |

La matriz vive en `application/acceso.py` (constantes `EDITORES`, `APROBADORES`, `CORREOS` y `CARGAS`): para cambiar
un permiso se edita ahí y se actualiza esta tabla. El servidor valida el rol en cada petición; la interfaz solo oculta
lo que el rol no puede usar.

**Roles que ya no existen.** La ACM y las tiendas dejaron de entrar a la app: ven sus casos en Power BI. Al arrancar,
la migración pasa los usuarios `editor` y `lector` al rol `atencion` (el de menor alcance: el administrador los
reasigna en Administración → Usuarios) y desactiva los usuarios `acm` y `tienda`. Sus comentarios y la bitácora se conservan.

### Power BI: tabla `caso_calculado`

Power BI lee SQL Server directamente y no puede repetir las reglas de la app (días hábiles, cruce con KGB, inventario,
alertas). Por eso la app deja una fila por caso en `caso_calculado` (casos abiertos y cerrados en los últimos 45 días)
con el estado y la fecha que ve la tienda, la solución, las fechas calculadas, el nivel y el texto de las alertas.

- Se reconstruye sola unos 15 segundos después de cada carga o cambio, y al arrancar la app.
- Para dejarla al día justo antes de la actualización de Power BI: `poetry run backlog-refrescar` (tarea programada).
- Las vistas de la ACM y de las tiendas se filtran **en Power BI**. Como Power BI se conecta a la base y no pasa por los
  permisos de la app, conviene darle una cuenta de SQL Server de solo lectura sobre `caso_calculado` (y no sobre las demás
  tablas), y filtrar por tienda con seguridad a nivel de fila (RLS) en el modelo.
- La tabla no lleva precios, PO ni comentarios.

---

## 3. Base de datos

### Motor y ubicación

- **Motor:** SQL Server (probado en SQL Server 2019) con el controlador `pymssql` y el pool de conexiones de SQLAlchemy. No requiere ODBC.
- **Conexión:** la cadena va en la variable `SQL_SERVER_STRING_CONNECTION` del archivo `.env` (formato SQLAlchemy, `mssql+pymssql://usuario:clave@servidor:1433/base`). El `.env` está excluido del repositorio; `.env.example` sirve de plantilla.
- **Esquema:** todas las tablas viven en el esquema `backlog` (variable `BACKLOG_DB_SCHEMA`), así la app no toca objetos de otros esquemas de la base.
- **Arranque:** al iniciar, la app crea el esquema, las tablas, índices y triggers que falten y agrega las columnas nuevas (lista `MIGRACIONES` en `infrastructure/esquema.py`). Nunca borra datos.
- **Fechas:** se guardan como texto ISO (`AAAA-MM-DD`), igual que en la versión SQLite, para no cambiar comparaciones ni la API.
- **Latencia:** el servidor está a unos 120 ms por viaje. Las escrituras masivas viajan en lotes y las lecturas de cada pantalla en un solo viaje; una carga de backlog de 3.000 líneas toma unos 20 s. Conviene instalar la app en un servidor cercano a la base.
- **Confidencialidad:** los archivos de datos (`.xlsx`, `.csv`, `.eml`, `.db`) y el `.env` están excluidos del repositorio (`.gitignore`).

### Tablas (27)

| Grupo | Tablas | Cómo se escriben |
|---|---|---|
| Casos | `cases`, `case_history`, `snapshots` | La carga del backlog inserta y actualiza los casos; los cambios de Apple quedan en `case_history` y la foto diaria en `snapshots`. Los casos nunca se borran: se cierran. |
| Gestión del equipo | `comments`, `notas_leidas`, `solicitudes_fecha`, `lotes`, `guide_arrivals`, `emails_log` | Las escriben los usuarios desde la app. Los comentarios no se pueden editar ni borrar (triggers). |
| Datos de Apple | `kgb`, `open_orders`, `open_order_history` | Cada carga reemplaza el KGB y el Open Orders vigentes; el historial de Open Orders acumula una foto por día. |
| Abastecimiento propio | `inventario`, `inventario_history`, `miami_purchases` | El inventario se reemplaza en cada carga y guarda su foto diaria. Las compras en Miami se registran a mano. |
| Maestros | `prices`, `parts`, `shipto`, `shipto_alias`, `holidays_extra`, `settings` | Precios y Parts Data se reemplazan completos en cada carga. Las tiendas se actualizan una por una. Lo demás se edita en Administración. |
| Publicación | `caso_calculado` | La reconstruye la app (no se edita a mano): una fila por caso para Power BI. |
| Acceso y control | `users`, `sessions`, `user_shipto` (sin uso desde que las tiendas salieron de la app), `loads`, `audit_log` | `audit_log` es la bitácora: no se puede editar ni borrar (triggers). `loads` registra cada archivo cargado. |

### Reglas de escritura

- **Archivos repetidos o viejos:** se rechazan un archivo ya cargado y uno con fecha anterior al último cargado del mismo tipo.
- **Atomicidad:** cada carga corre en una transacción; si algo falla, la base queda como estaba.
- **Bitácora:** cada cambio de gestión registra el antes y el después.
- **Fechas protegidas:** la recepción y el despacho registrados por un editor solo cambian con una solicitud aprobada por el administrador.
- **Tandas en bloque:** se pueden deshacer el mismo día. Si un campo cambió después, no se toca, y los comentarios quedan.

### Respaldos

- **Los hace SQL Server:** los respaldos y la restauración quedan en la política de TI para la base. La app ya no copia archivos antes de cada carga.
- **Cargas atómicas:** cada carga corre en una transacción; si falla, no queda nada a medias.

### Uso desde otras herramientas

- **Power BI:** se conecta directo a SQL Server (esquema `backlog`), de preferencia con un usuario de solo lectura. También se puede usar la exportación CSV de la pantalla Casos.
- **Migrar los datos de la versión SQLite:** `poetry run backlog-migrar-sqlite RUTA\data\backlog.db` copia todas las tablas (con sus id) al esquema configurado. Se niega si el esquema ya tiene datos y lo hace todo en una transacción.

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
- **Bitácora:** de solo lectura (triggers `INSTEAD OF UPDATE, DELETE`). Registra ingresos, cargas, cambios, correos, solicitudes, aprobaciones y administración.
- **Credenciales de la base:** solo en el `.env`, nunca en el código ni en Git. Se recomienda una cuenta propia para la app con permisos solo sobre el esquema `backlog`.

---

## 6. Instalación y operación

**En un equipo (Windows).** Requiere Python 3.10 o superior (probado en 3.10, 3.11 y 3.14.8) y
[Poetry](https://python-poetry.org) 2.x.

1. Copiar la carpeta a un lugar fijo, por ejemplo `C:\Herramientas\Backlog\backlog-asp`.
2. Copiar `.env.example` como `.env` y poner la cadena de conexión a SQL Server.
3. Doble clic en `iniciar.bat`. Instala Poetry si falta, instala las dependencias en `.venv` y, la primera vez,
   pide crear el usuario administrador.
4. Abrir http://127.0.0.1:8765.

Equivalente en consola:

```
poetry install                 # crea .venv con las versiones exactas de poetry.lock
poetry run backlog-admin       # crea el administrador (y el esquema si no existe)
poetry run backlog-asp         # inicia el tablero
```

Para usar otra versión de Python: `poetry env use C:\ruta\python.exe` y luego `poetry install`. El
`poetry.lock` ya trae las versiones correctas de cada dependencia para 3.10 a 3.14.

**En un servidor (para que la use el equipo).** Lo hace TI:

1. Instalar en un servidor o máquina virtual interna, como servicio, con Python, Poetry y `poetry install --only main`.
   1–2 vCPU y 2 GB de RAM son suficientes. Mejor en la misma red del SQL Server, por la latencia.
2. Definir el `.env` (o las mismas variables de entorno del servicio).
3. Publicarla detrás de un proxy inverso con HTTPS (IIS, nginx u otro) y definir `BACKLOG_COOKIE_SECURE=1`.
   El proxy es lo único que llega a `127.0.0.1:8765`.

No cambie `HOST` en `src/backlog_asp/config.py` para abrirla a la red sin HTTPS: las contraseñas viajarían en claro.

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

## 8. Estructura del código (arquitectura limpia)

Cada capa solo depende de las de adentro: `presentation` → `application` → `domain`. La infraestructura
implementa el puerto de persistencia (`application/puertos.py`) y la presentación la conecta con los casos de uso.

```
src/backlog_asp/
  domain/                  reglas de negocio puras (sin base de datos ni HTTP)
    calendario.py          días hábiles y festivos de Colombia
    partes.py              números de parte, guías y soluciones de Apple
    casos.py               cruce con el KGB, fechas, alertas, precio y prioridad de un caso
    gestion.py             validación, fechas protegidas y reglas entre campos
    abastecimiento.py      reglas de Open Orders (recovery, tipo de orden)
    tiendas.py             comparación de nombres de tiendas
    seguridad.py           contraseñas (scrypt) y tokens
    catalogos.py           estados, roles y parámetros por defecto
    errores.py             errores de negocio con su código HTTP
  application/             casos de uso
    puertos.py             interfaz de la conexión que necesitan los casos de uso
    contexto.py            foto de datos por petición y listado de casos
    cargas.py              carga de backlog, precios, Parts Data y tiendas
    abastecimiento.py      carga de KGB y Open Orders, abastecimiento por parte
    inventario.py          carga del inventario propio y asignación a casos
    gestion.py             cambios, solicitudes, avisos y selección múltiple
    casos.py               detalle, comentarios, guías y exportación
    correos.py, indicadores.py, miami.py, tiendas.py, acceso.py, sesiones.py, administracion.py
    archivos.py            lectura de xlsx/csv
    auditoria.py           bitácora y parámetros
  infrastructure/
    database.py            SQL Server: pool, lotes de escritura, varias lecturas en un viaje
    esquema.py             tablas, índices, triggers y migraciones en T-SQL
  presentation/
    api/app.py             FastAPI: cabeceras de seguridad, errores y páginas
    api/dependencias.py    conexión por petición, sesión, CSRF y roles
    api/routers/           rutas HTTP por área
    static/                interfaz: HTML, CSS y JavaScript sin librerías externas
  cli/                     backlog-admin y backlog-migrar-sqlite
  config.py                variables de entorno (.env)
tests/                     prueba de punta a punta contra SQL Server
```

Dependencias (`pyproject.toml`, versiones fijas en `poetry.lock`): FastAPI, Uvicorn, pandas, numpy, openpyxl,
defusedxml, python-multipart, SQLAlchemy, pymssql y python-dotenv.

## 9. Pruebas

`tests/test_flujo.py` recorre el flujo completo contra SQL Server en un esquema temporal (`backlog_test_…`)
que se borra al terminar: nunca toca el esquema `backlog`. Genera un backlog, KGB, Open Orders, precios,
tiendas e inventario de prueba. Valida login y CSRF, cargas y rechazo de archivos repetidos o viejos,
cruce con el KGB, gestión, guías, correos, compras en Miami, selección múltiple, roles (administrador, supervisor, comprador y atención al cliente), fechas protegidas con solicitud a supervisor o administrador, la tabla `caso_calculado`, bloqueo por intentos fallidos y que la bitácora y los
comentarios no se pueden borrar:

```
poetry run pytest
```

Para probar con archivos reales: definir `BACKLOG_TEST_BACKLOG` y `BACKLOG_TEST_KGB` con sus rutas.
La variable `BACKLOG_HOY` fija la fecha de "hoy" y existe solo para pruebas.
