"""Aplicación FastAPI: cabeceras de seguridad, páginas, archivos estáticos, rutas y manejo de errores."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ... import config
from ...domain.errores import ErrorNegocio
from ...infrastructure import database, esquema
from .routers import abastecimiento, admin, auth, cargas, casos, correos, gestion, tiendas

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


def preparar_base() -> None:
    conn = database.connect()
    try:
        esquema.init_db(conn)
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    preparar_base()
    yield


def create_app(init_db: bool = True) -> FastAPI:
    app = FastAPI(title="Backlog ASP", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=_lifespan if init_db else None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ErrorNegocio)
    async def error_negocio(request: Request, exc: ErrorNegocio):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": getattr(exc, "detail", "No encontrado.")}, status_code=404)
        return RedirectResponse("/")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(config.STATIC_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(config.STATIC_DIR / "login.html")

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    for r in (auth, casos, gestion, correos, abastecimiento, tiendas, cargas, admin):
        app.include_router(r.router)
    return app


app = create_app()
