"""Entrenamiento desatendido (noche): iteraciones de destilación con vigilancia.

Cada iteración:
  1) N vidas con el PROFESOR (7B local)     -> trayectorias buenas
  2) dataset + QLoRA del ALUMNO (3B)          -> adaptador
  3) redespliegue como qwen3b                 -> el sim piensa con la versión nueva
  4) 1 vida de EVALUACIÓN con el alumno       -> se ve en "generaciones anteriores" del panel

Vigila Docker Desktop y Ollama (0.0.0.0) y los relanza si se caen. Para parar: crear el
fichero training/data/STOP. Log en training/data/overnight.log.

    training/.venv/Scripts/python.exe training/overnight.py --iterations 3 --lives 3 --duration 2d
"""
from __future__ import annotations

import argparse
import atexit
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LOG = DATA / "overnight.log"
STOP = DATA / "STOP"
DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (Windows)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # una consola en cp1252 no puede con un acento: que se pierda el carácter, no la
        # noche entera de entrenamiento (una tilde tumbaba todo el proceso).
        enc = (sys.stdout.encoding or "ascii")
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def up(url: str, timeout: float = 3) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def ensure_ollama() -> None:
    if up("http://127.0.0.1:11434/api/tags"):
        return
    log("Ollama caído: lo relanzo en 0.0.0.0")
    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
    env = {**os.environ, "OLLAMA_HOST": "0.0.0.0:11434"}
    subprocess.Popen(["ollama", "serve"], env=env, creationflags=DETACHED,
                     stdout=open(DATA / "ollama.log", "ab"), stderr=subprocess.STDOUT)
    for _ in range(30):
        time.sleep(2)
        if up("http://127.0.0.1:11434/api/tags"):
            log("Ollama arriba"); return
    log("AVISO: Ollama no responde tras relanzarlo")


def ensure_docker() -> None:
    if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return
    log("Docker caído: relanzo Docker Desktop")
    exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if Path(exe).exists():
        subprocess.Popen([exe], creationflags=DETACHED)
    for _ in range(60):
        time.sleep(5)
        if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
            log("Docker arriba"); time.sleep(10); return
    log("AVISO: Docker no responde tras relanzarlo")


def run(cmd: list[str], label: str) -> int:
    log(f"[{label}] {' '.join(Path(c).name if os.sep in c else c for c in cmd)}")
    p = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(f"  {line}")
    rc = p.wait()
    log(f"[{label}] fin rc={rc}")
    return rc


CERROJO = DATA / "overnight.pid"


def vivo(pid: int) -> bool:
    """¿Sigue ahí ese proceso? En Windows se pregunta a la lista de tareas."""
    try:
        salida = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                capture_output=True, text=True, timeout=15).stdout
        return str(pid) in salida
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def tomar_cerrojo() -> bool:
    """Un solo entrenamiento a la vez.

    Dos noches simultáneas se destrozan entre ellas: cada una hace `down -v` y le tira el
    mundo a la otra, que se queda esperando a un contenedor que ya no existe y acaba en
    'timeout' sin haber jugado nada. Pasó de verdad, dos veces."""
    DATA.mkdir(parents=True, exist_ok=True)
    if CERROJO.exists():
        try:
            otro = int(CERROJO.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            otro = 0
        if otro and otro != os.getpid() and vivo(otro):
            log(f"ya hay un entrenamiento en marcha (proceso {otro}). Párala antes de empezar otra.")
            return False
        log("había un cerrojo de una noche que ya no existe; lo retiro")
    CERROJO.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(soltar_cerrojo)
    return True


def soltar_cerrojo() -> None:
    try:
        if CERROJO.exists() and CERROJO.read_text(encoding="utf-8").strip() == str(os.getpid()):
            CERROJO.unlink()
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--lives", type=int, default=3)
    ap.add_argument("--duration", default="10d")
    ap.add_argument("--modo", choices=["completo", "inversor"], default="completo",
                    help="inversor = mundo sin tienda/anuncios/dominios; solo bolsa y cuentas")
    ap.add_argument("--active-speed", type=float, default=1.0,
                    help="velocidad del reloj mientras la IA trabaja (x1 = fiel)")
    ap.add_argument("--brain", default="auto", choices=["auto", "teacher", "claude", "api"],
                    help="auto = freellm (api) si hay clave en .env, si no Claude CLI, si no el 7B local")
    a = ap.parse_args()
    if not tomar_cerrojo():
        raise SystemExit(1)
    py = sys.executable
    # señal de ingresos: en entrenamiento las ventas cuajan cada 3 días (14 en el sim real),
    # si no una vida de 10 días no vería un solo ingreso y la recompensa solo premiaría sobrevivir.
    os.environ.setdefault("ECONOSIM_RESOLVE_DAYS", "3")
    os.environ.setdefault("ECONOSIM_IDLE_SPEED", "3600")
    if a.brain == "auto":
        envf = HERE.parent / ".env"
        has_freellm = envf.exists() and any(l.startswith("ECONOSIM_UPSTREAM_API_KEY=") and l.split("=",1)[1].strip()
                                            for l in envf.read_text(encoding="utf-8").splitlines())
        import shutil
        if has_freellm:
            a.brain = "api"; log("cerebro profesor: freellm/auto (tu endpoint, fuerte y gratis)")
        elif shutil.which("claude"):
            a.brain = "claude"; log("cerebro profesor: Claude vía CLI (fallback 7B)")
        else:
            a.brain = "teacher"; log("cerebro profesor: 7B local")
    if STOP.exists():
        STOP.unlink()
    log(f"=== NOCHE: {a.iterations} iteraciones × ({a.lives} vidas {a.brain} × {a.duration}"
        f"{' · MODO INVERSOR' if a.modo == 'inversor' else ''}"
        f"{f' · reloj x{a.active_speed:g} en activo' if a.active_speed != 1 else ''} + QLoRA + eval) ===")
    for it in range(1, a.iterations + 1):
        if STOP.exists():
            log("STOP encontrado: paro"); break
        ensure_docker(); ensure_ollama()
        log(f"--- iteración {it}/{a.iterations} ---")
        rc = run([py, str(HERE / "pipeline.py"), "--lives", str(a.lives), "--duration", a.duration,
                  "--brain", a.brain, "--modo", a.modo, "--active-speed", str(a.active_speed)], f"it{it}")
        if rc != 0:
            log(f"iteración {it} falló (rc={rc}); sigo con la siguiente tras comprobar servicios")
            continue
        ensure_docker(); ensure_ollama()
        # vida de evaluación del alumno recién entrenado
        run([py, str(HERE / "run_episodes.py"), "--n", "1", "--duration", a.duration,
             "--prefix", f"eval{it}", "--brain", "student", "--timeout", "7200"], f"eval{it}")
    log("=== NOCHE terminada ===")


if __name__ == "__main__":
    main()
