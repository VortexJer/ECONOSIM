"""Una ITERACIÓN completa de entrenamiento, de principio a fin (la lanza el panel).

    1) juega N vidas con el cerebro actual (qwen3b) y cosecha trayectorias + puntuación
    2) construye el dataset con las vidas que puntuaron alto (SFT por rechazo)
    3) entrena QLoRA en la GPU sobre el modelo base HF
    4) fusiona y vuelve a servirlo COMO EL MISMO `qwen3b` (el modelo no cambia de nombre:
       el sim sigue apuntando a qwen3b, solo que ahora es la versión mejorada)

Imprime líneas "[etapa] ..." para que el panel las muestre en vivo.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = HERE / ".venv" / "bin" / "python"


def say(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


def run(cmd: list[str], stage: str, cwd: Path = HERE) -> int:
    """Ejecuta y reenvía cada línea de salida etiquetada con la etapa."""
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            say(stage, line)
    return p.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lives", type=int, default=4)
    ap.add_argument("--duration", default="2d")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--modo", choices=["completo", "inversor"], default="inversor")
    ap.add_argument("--active-speed", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=14400,
                    help="segundos reales máximos por vida; una vida de 30 días no cabe en una hora")
    ap.add_argument("--skip-play", action="store_true", help="usar las vidas ya jugadas")
    ap.add_argument("--skip-deploy", action="store_true")
    ap.add_argument("--brain", choices=["student", "teacher", "claude", "api"], default="student",
                    help="teacher = destilación: las vidas las juega el 7B local y el 3B las imita")
    a = ap.parse_args()
    t0 = time.time()
    tag = time.strftime("%Y%m%d-%H%M")

    say("inicio", f"iteración {tag}: {a.lives} vidas × {a.duration} con cerebro {a.brain}, "
                  f"luego QLoRA del alumno y redespliegue como qwen3b")

    # 1) jugar vidas
    if not a.skip_play:
        say("vidas", "jugando vidas con el cerebro actual…")
        rc = run([sys.executable, "run_episodes.py", "--n", str(a.lives), "--duration", a.duration,
                  "--prefix", f"it{tag}", "--timeout", str(a.timeout), "--brain", a.brain,
                  "--modo", a.modo, "--active-speed", str(a.active_speed)], "vidas")
        if rc != 0:
            say("error", "fallaron las vidas"); sys.exit(rc)

    # 2) dataset
    say("dataset", "construyendo el dataset con las vidas buenas…")
    ds_args = [sys.executable, "build_dataset.py"]
    if a.brain in ("teacher", "claude", "api"):
        ds_args += ["--only-brain", a.brain, "--min-score=-1e9"]   # todas las del profesor
    rc = run(ds_args, "dataset")
    sft = HERE / "data" / "sft.jsonl"
    n = sum(1 for _ in open(sft, encoding="utf-8")) if sft.exists() else 0
    if rc != 0 or n == 0:
        say("error", "dataset vacío: hacen falta más vidas o menos exigencia"); sys.exit(1)
    say("dataset", f"{n} sesiones de entrenamiento")

    # 3) entrenar
    say("entrenar", "QLoRA en la GPU (esto es lo lento)…")
    adapter = HERE / "adapters" / tag
    rc = run([str(VENV_PY), "train_qlora.py", "--data", str(sft), "--out", str(adapter),
              "--epochs", str(a.epochs)], "entrenar")
    if rc != 0:
        say("error", "falló el entrenamiento"); sys.exit(rc)

    # 4) redesplegar como el MISMO qwen3b
    if not a.skip_deploy:
        say("desplegar", "fusionando y sirviendo la versión entrenada como qwen3b…")
        rc = run([str(VENV_PY), "deploy.py", "--adapter", str(adapter), "--name", "qwen3b"], "desplegar")
        if rc != 0:
            say("error", "falló el despliegue"); sys.exit(rc)

    say("fin", f"iteración {tag} completa en {(time.time()-t0)/60:.1f} min · qwen3b actualizado")


if __name__ == "__main__":
    main()
