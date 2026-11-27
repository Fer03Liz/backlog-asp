"""Prepara el entorno .venv sin Poetry, para el modo pruebas en este equipo. Lo usa iniciar_pruebas.bat.

- Instala las versiones exactas de poetry.lock (las mismas del servidor), como `poetry install --only main`.
  Con --dev agrega también las de pruebas (pytest, httpx).
- Solo llama a pip cuando poetry.lock cambió desde la última vez (o con --forzar).
- Deja `src` en el camino de Python del entorno, así `python -m backlog_asp` funciona sin instalar el proyecto.

Uso: .venv\\Scripts\\python src\\backlog_asp\\cli\\entorno.py [--dev] [--forzar]
Solo usa la biblioteca estándar: corre antes de que existan las dependencias.
"""
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
SELLO = Path(sys.prefix) / "backlog_asp_requisitos.txt"
PTH = "backlog_asp_src.pth"


def requisitos(grupos: tuple) -> list:
    """Paquetes de poetry.lock de esos grupos, con su versión exacta y sus condiciones (versión de Python, sistema)."""
    lock = tomllib.loads((RAIZ / "poetry.lock").read_text(encoding="utf-8"))
    out = []
    for p in lock["package"]:
        suyos = [g for g in p.get("groups", ["main"]) if g in grupos]
        if not suyos:
            continue
        marca = p.get("markers")
        if isinstance(marca, dict):                       # condición distinta por grupo
            por_grupo = [marca.get(g) for g in suyos]
            marca = None if None in por_grupo else " or ".join(f"({m})" for m in por_grupo)
        out.append(f"{p['name']}=={p['version']}" + (f" ; {marca}" if marca else ""))
    return out


def enlazar_src() -> None:
    destino = Path(sysconfig.get_paths()["purelib"]) / PTH
    texto = str(RAIZ / "src") + "\n"
    if not destino.exists() or destino.read_text(encoding="utf-8") != texto:
        destino.write_text(texto, encoding="utf-8")


def main() -> int:
    if sys.version_info < (3, 11):
        print("Se necesita Python 3.11 o superior.")
        return 1
    grupos = ("main", "dev") if "--dev" in sys.argv else ("main",)
    texto = "\n".join(requisitos(grupos)) + "\n"
    if "--forzar" in sys.argv or not SELLO.exists() or SELLO.read_text(encoding="utf-8") != texto:
        print("Instalando dependencias con las versiones de poetry.lock (solo la primera vez o si cambia el lock)...")
        pendiente = SELLO.with_suffix(".pendiente.txt")
        pendiente.write_text(texto, encoding="utf-8")
        r = subprocess.call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(pendiente)])
        if r:
            print("pip no pudo instalar las dependencias (revise la conexión a internet o el proxy).")
            return r
        pendiente.replace(SELLO)
    enlazar_src()
    return 0


if __name__ == "__main__":
    sys.exit(main())
