"""¿Qué % saca al año, con TODOS los gastos? (comisiones, tasas, horquilla y el servidor)

Los 43 bloques de 6 meses (2005-2026) son consecutivos: se encadenan como si al final de
cada semestre se reinvirtiera todo en el siguiente. Cada bloque = media de 16 vidas con 40
empresas al azar. Se calcula con el capital del contrato (50 €) y con 5.000 € (donde el
servidor pesa ~1,4 %/año en vez de ~144 %/año).

Uso: training/.venv/Scripts/python.exe training/anual.py
"""
from __future__ import annotations

import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import laya_es as E                                                   # noqa: E402
from econosim.market.calendar import UTC                              # noqa: E402
from econosim.world import World                                      # noqa: E402

SESSIONS, LIVES, SUBSET, SEED = 125, 16, 40, 7
ETF = {"SPY", "QQQ", "DIA", "IWM"} | E.INVERSE
SELL, HOLD, BUY = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]


def main() -> int:
    md, fu = E.MarketData(), E.Fundamentals()
    grid = E.Grid(md)
    cache = E.build_num_cache(md, fu, grid, "all", SESSIONS)
    col = {n: i for i, n in enumerate(cache["names"])}
    M = cache["M"]
    mom = {}
    for (sym, dk, held), r in cache["index"].items():
        if held == "0" and sym not in ETF:
            m1y, m20 = M[r, col["mom_1y"]], M[r, col["mom_20d"]]
            if not (np.isnan(m1y) or np.isnan(m20)):
                mom.setdefault(dk, {})[sym] = (1 + m1y / 100) / (1 + m20 / 100) - 1

    def momentum(n):
        def mk(sub):
            def look(s, dk, held):
                if s in ETF or s not in sub:
                    return None
                rk = [x for x, _ in sorted(((x, v) for x, v in mom.get(dk, {}).items() if x in sub), key=lambda t: -t[1])]
                return BUY if s in rk[:n] else (SELL if held and s not in rk[:n * 3] else HOLD)
            return look
        return mk

    def nucleo(sat):
        def mk(sub):
            f = sat(sub)
            return lambda s, dk, h: [0, 0, 9.0] if s == "SPY" else f(s, dk, h)
        return mk

    estrategias = {
        "Índice (todo al S&P 500)": (None, "index", 1.0, None),
        "Momentum top 3 (bot)": (momentum(3), "", 1 / 3, None),
        "Núcleo 75 % + momentum 2 (bot / bot+IA)": (nucleo(momentum(2)), "", 0.125, {"SPY": 0.75}),
        "Quedarse en efectivo": (None, "cash", 1.0, None),
    }
    pricing = World(datetime(2020, 1, 1, tzinfo=UTC)).load_pricing("hetzner")
    i0 = next(i for i, d in enumerate(grid.days) if d >= E.SPLITS["all"][0] and i % E.DECIDE_EVERY == 0)
    bloques = list(range(i0, len(grid.days) - SESSIONS - 1, SESSIONS))
    empresas = sorted({s for d in mom.values() for s in d})
    rng = random.Random(SEED)
    subs = {b: [set(rng.sample(empresas, SUBSET)) for _ in range(LIVES)] for b in bloques}
    años = SESSIONS * len(bloques) / 252
    spy = md.series["SPY"]
    sp = np.prod([spy.asof(grid.days[min(b + SESSIONS, len(grid.days) - 1)]).close / spy.asof(grid.days[b]).close
                  for b in bloques])
    print(f"{len(bloques)} semestres encadenados ({grid.days[bloques[0]]} → {grid.days[bloques[-1] + SESSIONS]}), {años:.1f} años")
    print(f"S&P 500 puro, sin ningún gasto: {(sp ** (1 / años) - 1) * 100:+.1f} %/año\n")
    for capital in (50.0, 5000.0):
        print(f"── Capital {capital:,.0f} € · con comisiones, tasas, horquilla y servidor (~6 €/mes) ──")
        for nombre, (mk, fixed, frac, fby) in estrategias.items():
            sim = E.FastSim(md, grid, pricing, capital, 0.0, buy_frac=frac, frac_by=fby)
            factor = 1.0
            peor = 1.0
            for b in bloques:
                if fixed:
                    fin = sim.run(b, SESSIONS, lambda *_: None, fixed=fixed)["final"]
                    g = fin / capital
                else:
                    g = float(np.mean([sim.run(b, SESSIONS, mk(sub))["final"] for sub in subs[b]])) / capital
                factor *= g
                peor = min(peor, g)
            print(f"  {nombre:42s} {(factor ** (1 / años) - 1) * 100:+7.1f} %/año · peor semestre {(peor - 1) * 100:+6.1f} %")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
