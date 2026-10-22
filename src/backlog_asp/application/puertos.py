"""Puerto de persistencia: lo que los casos de uso necesitan de la base. Lo implementa infrastructure.database.

Las consultas usan `?` para los parámetros y nombres de tabla sin esquema.
"""
from typing import Any, Iterable, Protocol, Sequence


class Resultado(Protocol):
    lastrowid: Any

    def fetchone(self): ...

    def fetchall(self) -> list: ...

    def __iter__(self): ...


class Conexion(Protocol):
    def execute(self, sql: str, params: Sequence = ()) -> Resultado:
        """Ejecuta ya y devuelve las filas (acceso por nombre y por posición)."""

    def defer(self, sql: str, params: Sequence = ()) -> None:
        """Escritura sin respuesta que puede enviarse en lote más tarde (antes de la siguiente consulta o del commit)."""

    def executemany(self, sql: str, seq: Iterable[Sequence]) -> None: ...

    def select_many(self, consultas: Sequence[tuple]) -> list:
        """Varias consultas en un solo viaje: [(sql, params), ...] -> [filas, ...]."""

    def upsert(self, table: str, row: dict, keys: tuple, defer: bool = False) -> None:
        """Inserta la fila o la reemplaza si ya existe una con esa llave."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
