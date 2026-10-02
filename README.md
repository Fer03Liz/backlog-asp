# Backlog ASP · v1

Tablero local para el seguimiento diario del backlog de Apple Spare Parts en Ingram Micro Colombia.
Corre en tu propio equipo (http://127.0.0.1:8765), guarda todo en una base SQLite dentro de `data/`
y no se conecta a internet.

## Qué hace

- **Casos**: todas las líneas del backlog con filtros (solución, tienda, estado, alertas, Super ASP),
  agrupación (tienda, solución, estado, alerta, guía, parte) y orden (prioridad, días al límite de fill rate,
  días a la ETA, edad). Cada fila tiene un *carril* de tres semanas que muestra hoy, la llegada, la ventana
  de fill rate (ETA a ETA + tolerancia) y el despacho posible en verde, ámbar o rojo.
- **Detalle del caso**: alertas, fechas calculadas, gestión del equipo (estado, responsable, recepción,
  despacho, fecha a comunicar, cambio de solución y aprobación de la KAM), cruce con el KGB,
  datos de Apple en solo lectura, comentarios e historial completo.
- **Guías**: los casos agrupados por el número de guía que la KAM escribe en *Comments*, con su estado en
  el KGB. Al registrar la llegada de una guía, todos sus casos pasan a *Recibido*.
- **Correos**: un correo por tienda, solo con tiempos (notificación, PO de la tienda y fecha estimada de
  despacho). Se copia el texto o se descarga un borrador `.eml` que Outlook abre listo para enviar.
- **Indicadores**: fill rate del backlog, líneas en riesgo en 7 días, valor pendiente, OH, espera de la KAM,
  flujo diario (entran/salen), edad, desempeño por solución, confiabilidad de la ETA, partes y tiendas.
- **Cargas**: backlog y KGB diarios, precios y tiendas, con historial.
- **Administración**: usuarios y roles, parámetros, días no hábiles propios y bitácora.

## Instalación (Windows)

1. Instala Python 3.10 o superior (si tu equipo es corporativo, pídelo a TI).
2. Descomprime esta carpeta en un lugar fijo, por ejemplo `C:\BacklogASP`.
3. Doble clic en `iniciar.bat`. La primera vez crea el entorno, instala dependencias y te pide
   crear el usuario administrador. Luego abre el navegador en http://127.0.0.1:8765.

Desde la consola, el equivalente es:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts\crear_admin.py
python run.py
```

## Rutina diaria

1. **Cargas → Backlog diario**: sube el archivo de la KAM (`backlog_report_460241_AAAA_MM_DD.xlsx`).
   La fecha se toma del nombre. La app:
   - actualiza Option, ETA, Comments y Notes de los casos que siguen, y guarda cada cambio en el historial;
   - crea los casos nuevos;
   - cierra los que ya no aparecen (*Asignado* si están en la hoja Allocated, si no *Salió del reporte*);
   - reabre y marca los que vuelven a aparecer;
   - **nunca** sobrescribe lo que registró el equipo.
   Rechaza archivos repetidos y archivos más viejos que el último cargado.
2. **Cargas → KGB diario**: sube el `KGB_Open_Order_CEVA-CLS_MM_DD_AAAA.xlsx`. Se filtra al cliente
   configurado (hoy 460241) y reemplaza al KGB anterior.
3. **Casos**: filtra *Solo rojas* y trabaja de arriba hacia abajo (ya viene en orden de prioridad).
4. **Guías**: registra las llegadas del día.
5. **Correos**: arma los correos de los casos gestionados hoy.

Antes de cada carga se guarda una copia de la base en `data/backups/` (se conservan las últimas 30).

## Reglas de negocio

**Cruce con el KGB.** Se hace por el número de guía que la KAM escribe en *Comments*
(por ejemplo `ACSP005943`, `GEOPA21647`) contra la columna `TRACKING_NR` del KGB, y dentro de la guía
por número de parte (sin prefijo: `LZ661-45268` = `661-45268`) o por un sustituto mencionado en el
comentario. La guía que aparece en *Notes* ("Delayed AWB …") es la guía retrasada original y no se usa
para el cruce.

**Fechas.**
- Llegada: recepción registrada → llegada de la guía registrada → OH (ya está en bodega) →
  EDD del KGB (solo In Transit con guía encontrada) → ETA de Apple.
- Despacho posible: el siguiente día hábil después de la llegada (no se despacha el mismo día ni en fines
  de semana o festivos de Colombia), y nunca antes de hoy.
- Límite de fill rate: ETA + tolerancia (2 días calendario por defecto; configurable como días hábiles).
- Una línea está **a tiempo** si su fecha de despacho registrada es igual o anterior al límite.

**Alertas.**

| Alerta | Nivel | Cuándo |
|---|---|---|
| Venció el plazo de fill rate | Rojo | Hoy es posterior al límite y no hay despacho |
| Despacho después del límite | Rojo | El despacho posible cae después del límite |
| OH demorado | Rojo | OH sin despachar por más de 1 día hábil |
| Sin margen | Ámbar | El despacho posible es justo el último día del plazo |
| Guía no aparece en el KGB | Ámbar | In Transit con guía que no está en el KGB cargado |
| Guía sin la parte | Ámbar | La guía está en el KGB pero no trae la parte ni sus sustitutos |
| ETA antes de la llegada KGB | Ámbar | La ETA de Apple es anterior al EDD del KGB |
| Guía retrasada | Ámbar | El KGB la marca fuera de SLA o Apple la reporta retrasada |
| Llegada sin recepción | Ámbar | Ya debió llegar y nadie registró la recepción |
| Miami con parte en KGB | Ámbar | Opción Miami y la misma parte viene en tránsito (evitar doble compra) |
| Esperando a la KAM | Ámbar | Aprobación pendiente por más de 2 días |
| Reabierto | Ámbar | El caso había salido del reporte y volvió |
| ETA en día no hábil | Info | La ETA cae en sábado, domingo o festivo |
| Despachado y sigue en el reporte | Info | Pasaron 3 días hábiles desde la ETA; confirmar con la tienda |

**Prioridad.** OH primero, luego rojos, luego ámbar, y dentro de cada grupo lo más cercano al límite.
Super ASP (estrategia Z8.2) desempata.

**Precio.** Se usa *exchange* si la garantía está en la lista configurada (por defecto `OO`) y *stocking*
en los demás casos. El archivo de precios necesita una columna de parte y columnas que contengan
"stock" y "exch" en el nombre (hay plantilla en Cargas).

## Seguridad

- Solo escucha en `127.0.0.1`: nadie más en la red puede abrirla.
- Contraseñas con scrypt y sal aleatoria; mínimo 12 caracteres con letras y números.
- Bloqueo de 15 minutos tras 5 intentos fallidos.
- Sesiones con cookie HttpOnly y SameSite=Strict; en la base solo queda el hash del token.
  Cierre por 60 minutos de inactividad y máximo 8 horas.
- Token CSRF en todas las escrituras; política de seguridad de contenido (CSP) estricta, sin scripts
  externos ni en línea; la interfaz nunca inserta datos como HTML.
- Roles: administrador, editor y lector. Las contraseñas temporales obligan a cambiarlas al ingresar.
- Bitácora de solo lectura (protegida con triggers en la base): ingresos, cargas, cambios, correos,
  exportaciones y administración. Los comentarios tampoco se editan ni se borran.
- Archivos de hasta 25 MB, solo `.xlsx` o `.csv`, leídos en memoria con protección contra XML malicioso.
- El CSV exportado neutraliza fórmulas para que Excel no las ejecute.
- La carpeta `data/` contiene información confidencial: no la compartas ni la subas a Git
  (`.gitignore` ya la excluye). Si la copias a otro lado, que sea a una ubicación corporativa cifrada.

## Uso en equipo (~6 personas)

Hoy la app está pensada para un solo equipo. Para que la use el equipo sin perder seguridad, el camino
recomendado es con TI:

1. Instalarla en un servidor interno o una máquina virtual de Ingram, no en un portátil.
2. Publicarla detrás de un proxy inverso con HTTPS (IIS, nginx o el que use TI) y definir
   `BACKLOG_COOKIE_SECURE=1`. El proxy es el único que debe llegar a `127.0.0.1:8765`.
3. Idealmente, autenticación corporativa (SSO de Microsoft Entra ID) en el proxy.
4. Respaldos de `data/` dentro de la política de TI.

No cambies `HOST` en `app/config.py` para abrirla a la red sin HTTPS: las contraseñas viajarían en claro.

## Power BI

La base `data/backlog.db` se puede leer desde Power BI Desktop con un controlador ODBC de SQLite
(por ejemplo *SQLite3 ODBC Driver*): Obtener datos → ODBC → DSN apuntando al archivo.
Tablas útiles: `cases` (estado actual), `snapshots` (foto diaria de Option y ETA por caso),
`case_history` (cambios de Apple), `loads` (resumen de cada carga), `kgb`, `prices` y `audit_log`.
Conéctate en modo lectura. Alternativa sin ODBC: Casos → Exportar CSV.

## Parámetros (Administración)

| Parámetro | Por defecto | Nota |
|---|---|---|
| Cliente KGB | 460241 | Cambia el 2-nov-2026 cuando el KGB pase a Ingram (ese día es festivo) |
| Tolerancia de fill rate | 2 | Días después de la ETA |
| Tolerancia en días hábiles | 0 | 0 = calendario, 1 = hábiles |
| Garantías exchange | OO | Separadas por coma |
| Días de espera de la KAM | 2 | |
| Días máximos de OH | 1 | Hábiles |

Los festivos de Colombia se calculan solos (Ley Emiliani y Semana Santa). Los cierres propios de la
bodega se agregan en Administración.

## Pruebas

`tests/test_flujo.py` recorre el flujo completo con archivos reales (usa una base temporal; requiere `pip install httpx`):

```
python tests/test_flujo.py backlog_report_460241_2026_09_28.xlsx KGB_Open_Order_CEVA-CLS_09_29_2026.xlsx
```

La variable `BACKLOG_HOY` fija la fecha de "hoy" y existe **solo para pruebas**; no la uses en el día a día.

## Estructura

```
app/        servidor (FastAPI), reglas de negocio, cargas, seguridad y base de datos
static/     interfaz (HTML, CSS y JavaScript sin librerías externas)
scripts/    crear_admin.py
tests/      prueba de punta a punta
data/       se crea al iniciar: base, respaldos (no compartir)
```
