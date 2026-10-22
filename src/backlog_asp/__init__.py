"""Tablero de backlog ASP.

Capas (cada una solo depende de las de adentro):
  domain/          reglas de negocio puras: calendario, partes, alertas, prioridad, contraseñas
  application/     casos de uso: cargas, gestión, abastecimiento, indicadores, correos, sesiones
  infrastructure/  SQL Server: conexión, esquema y bitácora
  presentation/    API HTTP (FastAPI) e interfaz web
"""
