"""Rentabilidad ANUALIZADA del dinero mientras está invertido (como la mide un gestor):
sin contar la caja parada ni el servidor, restando comisiones y horquilla. Compara la
campeona de un cerebro con la estrategia índice del simulador y con el S&P 500 puro
(comprar y mantener SPY) en EXACTAMENTE las mismas vidas.

Uso: training/.venv/Scripts/python.exe training/rentabilidad.py <carpeta de ejecución factor-/num->
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import laya_es as E                                                   # noqa: E402
from econosim.market.calendar import UTC                              # noqa: E402
from econosim.world import World                                      # noqa: E402


def anual(r: float, dias: int) -> float:
    return (1 + r) ** (252 / dias) - 1 if dias > 0 and r > -1 else float("nan")


def main() -> int:
    run = Path(sys.argv[1])
    pr = json.loads((run / "progress.json").read_text(encoding="utf-8"))
    a = type("A", (), pr["config"])
    if getattr(a, "train_from", ""):
        E.SPLITS["train"] = (E.date.fromisoformat(a.train_from), E.SPLITS["train"][1])
    md, fu = E.MarketData(), E.Fundamentals()
    grid = E.Grid(md)
    caches = {s: E.build_num_cache(md, fu, grid, s, a.sessions) for s in ("train", "val", "test")}
    if a.brain == "factor":
        for c in caches.values():
            E.build_factor_matrix(c)
        brain = E.FactorBrain()
    else:
        E.normalize_num(caches)
        brain = E.MLPScorer(caches["train"]["M"].shape[1], hidden=a.hidden)
    theta = torch.load(run / "champion_mlp.pt")
    sim = E.FastSim(md, grid, World(datetime(2020, 1, 1, tzinfo=UTC)).load_pricing("hetzner"), a.initial_eur, a.min_invested)
    rng = random.Random(a.seed)
    starts = {"val": sorted(rng.sample(grid.starts("val"), a.val_lives)),
              "test": sorted(rng.sample(grid.starts("test"), a.val_lives))}
    spy = md.series["SPY"]
    out = {"run": run.name, "campeona_gen": pr["champion"]["gen"]}
    for split, st in starts.items():
        c = caches[split]
        P = brain.probs(theta, torch.from_numpy(c["M"]))

        def look(s, dk, h):
            r = c["index"].get((s, dk, "1" if h else "0"))
            return None if r is None else P[r]
        filas = []
        for i in st:
            ch = sim.run(i, a.sessions, look, track=True)
            ix = sim.run(i, a.sessions, lambda *_: None, fixed="index", track=True)
            d0, d1 = grid.days[i], grid.days[min(i + a.sessions, len(grid.days) - 1)]
            spy_r = spy.asof(d1).close / spy.asof(d0).close - 1
            cs = sim.run(i, a.sessions, lambda *_: None, fixed="cash")
            filas.append((ch["twr"], ch["dias_invertido"], ix["twr"], ix["dias_invertido"], spy_r,
                          ch["final"], ix["final"], cs["final"]))
        f = np.array(filas, dtype=float)
        ses = a.sessions

        def agregada(r, d):
            # TODAS las vidas juntas: suma de log-rentabilidades / suma de días invertidos, anualizada.
            # (Promediar rentabilidades ya anualizadas infla el número: +5 % en 15 días -> +130 %/año.)
            m = d > 0
            return (np.exp(np.log1p(r[m]).sum() * 252 / d[m].sum()) - 1) * 100
        res = {
            "campeona_anual_sobre_lo_invertido_%": round(float(agregada(f[:, 0], f[:, 1])), 1),
            "campeona_tiempo_invertida_%": round(float(f[:, 1].mean() / ses * 100), 0),
            "indice_sim_anual_sobre_lo_invertido_%": round(float(agregada(f[:, 2], f[:, 3])), 1),
            "sp500_puro_anual_%": round(float(agregada(f[:, 4], np.full(len(f), ses))), 1),
            "campeona_semestre_mediana_%": round(float(np.median(f[:, 0])) * 100, 1),
            "sp500_semestre_mediana_%": round(float(np.median(f[:, 4])) * 100, 1),
            "vidas_campeona_gana_a_sp500": f"{int((f[:, 0] > f[:, 4]).sum())}/{len(f)}",
            # vida a vida, en euros y en las MISMAS fechas: ¿gana siempre o unas sí y otras no?
            "vidas_gana_al_indice_€": f"{int((f[:, 5] > f[:, 6]).sum())}/{len(f)}",
            "vidas_gana_a_quedarse_en_efectivo_€": f"{int((f[:, 5] > f[:, 7]).sum())}/{len(f)}",
            "ventaja_vs_indice_€_por_vida": [round(float(x), 2) for x in sorted(f[:, 5] - f[:, 6])],
            "peor_vida_vs_indice_€": round(float((f[:, 5] - f[:, 6]).min()), 2),
            "mejor_vida_vs_indice_€": round(float((f[:, 5] - f[:, 6]).max()), 2),
            "mediana_vs_indice_€": round(float(np.median(f[:, 5] - f[:, 6])), 2),
        }
        out[split] = res
    (run / "rentabilidad.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
