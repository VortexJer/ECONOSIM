"""G6/G7 (fase 14): Laya APRENDE algo y el entrenamiento por generaciones sigue vivo.

Lee la última ejecución de training/laya_runs/ (o la indicada como argumento):
  G6  hay una campeona de generación >= 1 (el afinado cambió la cabeza Y ganó a Laya sin
      entrenar en validación), opera de verdad (compra en > 2 % de las decisiones y hace
      operaciones) y en validación acaba con más dinero que quedarse en efectivo.
  G7  (--running) la ejecución sigue en marcha: estado "entrenando" y progreso escrito
      hace menos de 45 minutos.
El número de TEST se imprime, pero no decide nada (no se usa para elegir campeona).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "training" / "laya_runs"


def fail(msg: str) -> None:
    print("FAIL:", msg)
    sys.exit(1)


args = [x for x in sys.argv[1:] if not x.startswith("--")]
runs = sorted((p for p in RUNS.glob("*") if (p / "progress.json").exists()), key=lambda p: p.stat().st_mtime)
run = Path(args[0]) if args else (runs[-1] if runs else None)
if run is None:
    fail("no hay ejecuciones en training/laya_runs")
pr = json.loads((run / "progress.json").read_text(encoding="utf-8"))

if "--running" in sys.argv:
    edad = time.time() - (run / "progress.json").stat().st_mtime
    if pr.get("status") != "entrenando":
        fail(f"estado {pr.get('status')!r}, no entrenando")
    if edad > 45 * 60:
        fail(f"progreso sin escribir desde hace {edad / 60:.0f} min")
    print(f"LAYA RUNNING OK ({run.name}, {len(pr['generations']) - 1} generaciones, progreso hace {edad / 60:.0f} min)")
    sys.exit(0)

ch = pr.get("champion") or {}
cash = pr["baselines"]["cash"]["val"]["score"]
if ch.get("gen", 0) < 1:
    fail(f"la campeona sigue siendo la generación 0 (Laya sin entrenar)")
v = ch["val"]
if v["actions"].get("buy", 0) <= 0.02 or v["trades"] <= 0:
    fail(f"la campeona no opera: acciones {v['actions']}, operaciones {v['trades']}")
if v["score"] <= cash:
    fail(f"la campeona no supera a quedarse en efectivo en validación: {v['score']:.2f} <= {cash:.2f}")
g0 = pr["generations"][0]["val"]["score"]
print(f"campeona gen {ch['gen']}: val {v['score']:.2f} € (gen0 {g0:.2f}, efectivo {cash:.2f}, índice "
      f"{pr['baselines']['index']['val']['score']:.2f}) · test {ch['test']['score']:.2f} € (efectivo "
      f"{pr['baselines']['cash']['test']['score']:.2f}, índice {pr['baselines']['index']['test']['score']:.2f}) · "
      f"acciones {v['actions']} · operaciones/vida {v['trades']:.1f}")
print("LAYA LEARNING OK")
