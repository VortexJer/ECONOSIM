"""¿La campeona LEE los datos o solo invierte?

Tres pruebas, todas fuera de muestra (validación 2019-21 y test 2022-25, que la evolución
no usó para aprender):

  1. IC (coeficiente de información, la medida estándar en gestión cuantitativa): en cada
     fecha de decisión, correlación de rangos (Spearman) entre su nota de "comprar" y lo que
     cada acción sube DESPUÉS (20 y 60 sesiones). IC medio > 0 con t > 2 = sus notas
     anticipan subidas y bajadas.
  2. Prueba del azar: se repiten sus vidas barajando entre acciones, dentro de cada fecha, las
     notas que dio. Misma cantidad invertida, mismas reglas; solo cambia QUÉ elige. Si la
     campeona gana a (casi) todas las barajadas, elegir importa; si no, solo está invirtiendo.
  3. Qué datos mueve su decisión: cuánto cambia la nota de comprar al mover cada variable
     una desviación típica (media en todos los estados).

Uso: training/.venv/Scripts/python.exe training/analiza_campeona.py <carpeta de la ejecución num-...>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import laya_es as E                                                  # noqa: E402


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = a.argsort().argsort().astype(float), b.argsort().argsort().astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d else 0.0


def main() -> int:
    run = Path(sys.argv[1])
    cfg = json.loads((run / "progress.json").read_text(encoding="utf-8"))["config"]
    a = type("A", (), cfg)
    md, fu = E.MarketData(), E.Fundamentals()
    grid = E.Grid(md)
    caches = {s: E.build_num_cache(md, fu, grid, s, a.sessions) for s in ("train", "val", "test")}
    E.normalize_num(caches)
    theta = torch.load(run / "champion_mlp.pt")
    sc = E.MLPScorer(caches["train"]["M"].shape[1], hidden=a.hidden)
    names = caches["train"]["names"]
    feat = names + ["falta:" + n for n in names]
    out = {"run": run.name, "champion_gen": json.loads((run / "progress.json").read_text(encoding="utf-8"))["champion"]["gen"]}

    for split in ("val", "test"):
        c = caches[split]
        P = sc.probs(theta, torch.from_numpy(c["M"]))
        # --- 1. IC: nota de comprar (sin posición) frente a la subida posterior
        por_dia = {}
        for (sym, dk, held), i in c["index"].items():
            if held == "0":
                por_dia.setdefault(dk, []).append((sym, P[i, E.BUY]))
        res = {}
        spy = md.series["SPY"]
        for H, residual in ((20, False), (60, False), (20, True), (60, True)):
            ics = []
            for dk, lst in por_dia.items():
                i0 = grid.idx.get(E.date.fromisoformat(dk))
                if i0 is None or i0 + 1 + H >= len(grid.days) or len(lst) < 8:
                    continue
                fw, pb = [], []
                for sym, p in lst:
                    s = md.series[sym]
                    b1, b2 = s.asof(grid.days[i0 + 1]), s.asof(grid.days[i0 + 1 + H])
                    if b1 and b2:
                        r = b2.close / b1.close - 1
                        if residual:
                            # quitar lo que explica el mercado: r - beta * r_indice (beta de 1 año, solo pasado)
                            k = s.index_of(grid.days[i0])
                            cs = [x.close for x in s.bars[max(0, k - 252): k + 1]]
                            ks = spy.index_of(grid.days[i0])
                            cm = [x.close for x in spy.bars[max(0, ks - 252): ks + 1]]
                            bc = E.__dict__.get("quant") or __import__("econosim.market.quant", fromlist=["x"])
                            bb = bc.beta_corr(cs, cm)
                            m1, m2 = spy.asof(grid.days[i0 + 1]), spy.asof(grid.days[i0 + 1 + H])
                            r -= (bb["beta"] if bb else 1.0) * (m2.close / m1.close - 1)
                        fw.append(r); pb.append(p)
                if len(fw) >= 8:
                    ics.append(spearman(np.array(pb), np.array(fw)))
            ics = np.array(ics)
            res[f"IC_{H}{'_sin_mercado' if residual else ''}"] = {"media": round(float(ics.mean()), 4), "t": round(float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))), 2),
                              "t_corregida_solape": round(float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) / np.sqrt(H / E.DECIDE_EVERY)), 2),
                              "fechas": len(ics), "positivas": round(float((ics > 0).mean()), 3)}
        # --- 2. prueba del azar: mismas vidas, notas barajadas entre acciones de cada fecha
        rng = np.random.default_rng(0)
        starts = sorted(__import__("random").Random(a.seed).sample(grid.starts("val"), a.val_lives)) if split == "val" else None
        ev = E.Evolution.__new__(E.Evolution)
        ev.a, ev.md, ev.fu, ev.grid = a, md, fu, grid
        from econosim.market.calendar import UTC
        from econosim.world import World
        from datetime import datetime
        ev.sim = E.FastSim(md, grid, World(datetime(2020, 1, 1, tzinfo=UTC)).load_pricing("hetzner"), a.initial_eur, a.min_invested)
        rr = __import__("random").Random(a.seed)
        v_st = sorted(rr.sample(grid.starts("val"), a.val_lives))
        t_st = sorted(rr.sample(grid.starts("test"), a.val_lives))
        starts = v_st if split == "val" else t_st

        def jugar(Pm):
            idx = c["index"]

            def look(s, dk, h):
                r = idx.get((s, dk, "1" if h else "0"))
                return None if r is None else Pm[r]
            return float(np.mean([ev.sim.run(i, a.sessions, look)["final"] for i in starts]))
        real = jugar(P)
        # barajar filas entre acciones dentro de cada (fecha, ¿tiene?)
        grupos = {}
        for (sym, dk, held), i in c["index"].items():
            grupos.setdefault((dk, held), []).append(i)
        barajadas = []
        for _ in range(30):
            Pb = P.copy()
            for rows in grupos.values():
                perm = rng.permutation(rows)
                Pb[rows] = P[perm]
            barajadas.append(jugar(Pb))
        barajadas = np.array(barajadas)
        res["azar"] = {"campeona": round(real, 2), "barajadas_media": round(float(barajadas.mean()), 2),
                       "barajadas_max": round(float(barajadas.max()), 2),
                       "gana_a": f"{int((real > barajadas).sum())}/{len(barajadas)}"}
        out[split] = res

    # --- 3. qué variables mueven la nota de comprar
    X = torch.from_numpy(caches["val"]["M"])
    base = sc.probs(theta, X)[:, E.BUY]
    sens = []
    for j, n in enumerate(feat):
        if n.startswith("falta:"):
            continue
        Xp = X.clone(); Xp[:, j] += 1.0
        sens.append((n, float((sc.probs(theta, Xp)[:, E.BUY] - base).mean())))
    sens.sort(key=lambda x: -abs(x[1]))
    out["sensibilidad_comprar"] = [{"variable": n, "efecto_+1sd": round(v, 4)} for n, v in sens[:12]]
    (run / "analisis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
