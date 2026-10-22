"""Inicia el tablero en http://127.0.0.1:8765 (solo accesible desde este equipo).

Uso: poetry run backlog-asp   (o python -m backlog_asp)
"""
import os
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"Backlog ASP en {url}  (cierre esta ventana o use Ctrl+C para detenerlo)")
    if not os.environ.get("BACKLOG_NO_BROWSER"):
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("backlog_asp.presentation.api.app:app", host=config.HOST, port=config.PORT, log_level="warning",
                proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
