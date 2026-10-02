"""Inicia el tablero en http://127.0.0.1:8765 (solo accesible desde este equipo)."""
import os
import threading
import webbrowser

import uvicorn

from app import config

if __name__ == "__main__":
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"Backlog ASP en {url}  (cierre esta ventana o use Ctrl+C para detenerlo)")
    if not os.environ.get("BACKLOG_NO_BROWSER"):
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning",
                proxy_headers=False, server_header=False)
