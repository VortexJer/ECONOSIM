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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--lives", type=int, default=3)
    ap.add_argument("--duration", default="2d")
    ap.add_argument("--brain", default="teacher", choices=["teacher", "claude"])
    a = ap.parse_args()
    py = sys.executable
    if STOP.exists():
        STOP.unlink()
    log(f"=== NOCHE: {a.iterations} iteraciones × ({a.lives} vidas {a.brain} × {a.duration} + QLoRA + eval) ===")
    for it in range(1, a.iterations + 1):
        if STOP.exists():
            log("STOP encontrado: paro"); break
        ensure_docker(); ensure_ollama()
        log(f"--- iteración {it}/{a.iterations} ---")
        rc = run([py, str(HERE / "pipeline.py"), "--lives", str(a.lives), "--duration", a.duration,
                  "--brain", a.brain], f"it{it}")
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
