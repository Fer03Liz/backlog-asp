# Modo pruebas (SQLite local)

Sirve para probar cambios en este equipo **sin acceso a SQL Server** antes de pasarlos a `main`.
La app usa una base SQLite aparte, en `data/pruebas/backlog_pruebas.db`.

**Qué no toca:**

- **SQL Server.** En modo pruebas la app no se conecta, aunque el `.env` tenga la cadena de conexión.
- **`data/backlog.db`** (la base de la versión anterior) ni los respaldos. La app se niega a abrirlos como base de pruebas.
- **El servidor.** El modo por defecto sigue siendo SQL Server. TI no tiene que cambiar nada: su `.env` no
  define `BACKLOG_DB_ENGINE`.

La carpeta `data/` y los archivos `*.db` están en `.gitignore`: la base de pruebas nunca se sube al repositorio.

## Uso diario

1. Doble clic en **`iniciar_pruebas.bat`**.
   - La primera vez pide crear el usuario administrador.
   - Abre `http://127.0.0.1:8766`, con una franja roja arriba y el aviso "MODO PRUEBAS" abajo a la izquierda.
2. Cargue los archivos del día (backlog, KGB, Open Orders…) desde **Cargas**, como en el tablero real.
3. Revise los cambios y, si todo está bien, haga commit en su rama y páselos a `main` como siempre.

## Comandos

| Comando | Qué hace |
|---|---|
| `poetry run python -m backlog_asp.cli.pruebas` | Muestra la ruta, el tamaño, las filas por tabla y los usuarios de la base de pruebas. |
| `poetry run python -m backlog_asp.cli.pruebas copiar data\backlog.db` | Copia a la base de pruebas los datos de una base SQLite anterior. Abre el origen en solo lectura y no lo modifica. Se niega si la base de pruebas ya tiene datos. |
| `poetry run python -m backlog_asp.cli.pruebas reiniciar` | Guarda la base actual en `data/pruebas/respaldos/` y deja el tablero listo para empezar vacío. Cierre antes el tablero de pruebas. |
| `set BACKLOG_DB_ENGINE=sqlite` y luego `poetry run pytest` | Corre todas las pruebas contra bases SQLite temporales. |

Para trabajar con los datos reales de octubre, ejecute `copiar` **antes** de abrir el tablero de pruebas por primera vez
(o después de `reiniciar`). Los usuarios y las claves serán los de esa base.

## Cómo funciona

- **Mismo código, mismo SQL.** La app sigue escribiendo T-SQL. El modo pruebas lo traduce a SQLite al ejecutarlo,
  así se prueba exactamente lo que va al servidor. Traduce:
  - `TOP n` y `TOP (?)` a `LIMIT`;
  - `OFFSET … FETCH` a `LIMIT … OFFSET`;
  - `ISNULL`, `LEFT`, `RIGHT`, `LEN`, `CHARINDEX`, `CONCAT`, `YEAR`, `MONTH` y `DAY`;
  - `N'texto'`, `[corchetes]`, `TRUNCATE TABLE` y las pistas `WITH (NOLOCK…)`;
  - el bloqueo `sp_getapplock`.
- **Avisa lo que fallaría en SQL Server.** Si una consulta nueva usa algo que solo existe en SQLite, el modo pruebas
  da un error que explica qué usar en su lugar. Por ejemplo: `LIMIT`, `||`, `INSERT OR REPLACE`, `ON CONFLICT`,
  `TRUE`/`FALSE`, `ifnull()`, `length()`, `substr()`, comillas dobles o `+` para unir textos. Así no llega a
  `main` algo que funciona aquí y falla en el servidor.
- **Avisa el T-SQL que no sabe traducir.** Por ejemplo `GETDATE()`, `DATEADD`, `OUTPUT INSERTED`, `MERGE` o `TOP`
  dentro de una subconsulta. El mensaje dice que es válido en SQL Server pero que aquí no se puede probar.
- **Mismo esquema.** Las tablas salen de `infrastructure/esquema.py`.
  - Si una rama cambia una tabla (por ejemplo, la restricción de roles de `v4`), la base de pruebas se ajusta sola y
    conserva los datos.
  - Si no puede ajustarla, lo dice y sugiere `reiniciar`.
- **Se comporta como SQL Server en lo importante:**
  - textos sin distinguir mayúsculas;
  - llaves foráneas activas;
  - bitácora y comentarios de solo lectura;
  - espera de 30 s por bloqueos;
  - `NaN` rechazado como parámetro.

## Diferencias que quedan

Antes de pasar a `main`, conviene que alguien con acceso corra `poetry run pytest` contra el SQL Server de
desarrollo. Estas son las diferencias que el modo pruebas no detecta:

| En SQL Server | En el modo pruebas |
|---|---|
| Recorta o rechaza el texto más largo que `NVARCHAR(n)`. | Lo guarda completo. |
| Ignora los espacios al final al comparar textos. | Los tiene en cuenta. |
| `''` en una columna numérica se vuelve 0. | Se guarda como texto. |
| Unir dos columnas de texto con `+` funciona. | Da un resultado distinto sin avisar. Arme el texto en Python. |
| El rendimiento depende del servidor remoto (unos 120 ms por viaje). | Todo es local y más rápido. |
