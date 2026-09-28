"""¿Cuánto del momentum era sesgo de supervivencia?

Mismo bot, dos universos, 2005-2026 en semestres encadenados (como `anual.py`):
  - HOY:      las 100 más negociadas de entre las que están HOY en el S&P 500 (lo que usábamos:
              empresas que sabemos que sobrevivieron).
  - ENTONCES: las 100 más negociadas de entre las que estaban en el S&P 500 ESE DÍA (incluye a las
              que luego quebraron, fueron compradas o expulsadas).
La diferencia entre las dos es el sesgo. Las que no tienen precios se cuentan y se avisa (no se
esconden): si faltan, lo que queda del sesgo es una COTA INFERIOR.

Bot: momentum 12-1 (rendimiento de 12 meses sin el último), top N a partes iguales, revisa cada 5
sesiones, vende si cae fuera del top 3N. Gastos: 0,10 % por operación (horquilla + tasas).
Si una empresa deja de cotizar estando en cartera se vende a su último precio.

Uso: training/.venv/Scripts/python.exe training/sesgo.py [--top 3] [--desde 2013-01-02]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIT = Path(__file__).resolve().parent.parent / "data" / "pit"
SESSIONS, EVERY, COST, UNIVERSE = 125, 5, 0.001, 100


def cargar():
    p = pd.read_parquet(PIT / "prices.parquet", columns=["date", "ticker", "close", "volume", "adj_close"])
    p["date"] = pd.to_datetime(p["date"])
    p = p[p.date >= "2003-06-01"]
    adj = p.pivot_table(index="date", columns="ticker", values="adj_close")
    dv = (p.close * p.volume).groupby([p.date, p.ticker]).sum().unstack()
    dv = dv.reindex_like(adj).rolling(60, min_periods=40).mean()
    m = pd.read_csv(PIT / "members.csv", parse_dates=["start_date", "end_date"])
    m["end_date"] = m["end_date"].fillna(pd.Timestamp("2100-01-01"))
    return adj, dv, m


def miembros(m, d):
    return set(m[(m.start_date <= d) & (m.end_date > d)].ticker)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--desde", default="2005-01-03", help="primer semestre (antes de ~2013 faltan muchos precios)")
    a = ap.parse_args()
    adj, dv, m = cargar()
    days = adj.index
    spy = adj["SPY"]
    mom = adj.shift(21) / adj.shift(252) - 1
    hoy = miembros(m, days[-1])
    i0 = days.searchsorted(pd.Timestamp(a.desde))
    bloques = list(range(i0, len(days) - SESSIONS, SESSIONS))
    años = SESSIONS * len(bloques) / 252
    A = adj.to_numpy()
    cols = {t: j for j, t in enumerate(adj.columns)}

    def universo(tipo, i):
        d = days[i]
        base = hoy if tipo == "hoy" else miembros(m, d)
        dvi = dv.iloc[i]
        cand = [t for t in base if t in cols and not np.isnan(dvi.get(t, np.nan)) and not np.isnan(mom.iat[i, cols[t]])]
        return sorted(cand, key=lambda t: -dvi[t])[:UNIVERSE], len(base)

    def semestre(tipo, b):
        cash, pos = 1.0, {}                        # pos: ticker -> nº de "acciones"
        last = {}
        for i in range(b, b + SESSIONS + 1):
            for t in list(pos):                    # dejó de cotizar: vender al último precio
                px = A[i, cols[t]]
                if np.isnan(px):
                    cash += pos.pop(t) * last[t] * (1 - COST)
                else:
                    last[t] = px
            if i == b + SESSIONS:
                break
            if (i - b) % EVERY == 0:
                uni, _ = universo(tipo, i)
                rk = sorted(uni, key=lambda t: -mom.iat[i, cols[t]])
                for t in [t for t in pos if t not in rk[:a.top * 3]]:
                    cash += pos.pop(t) * A[i, cols[t]] * (1 - COST)
                nuevos = [t for t in rk[:a.top] if t not in pos]
                huecos = a.top - len(pos)
                for t in nuevos[:huecos]:
                    px = A[i, cols[t]]
                    gasta = cash / huecos
                    pos[t] = gasta * (1 - COST) / px
                    last[t] = px
                    cash -= gasta
                    huecos -= 1
        return cash + sum(q * last[t] for t, q in pos.items())

    print(f"{len(bloques)} semestres ({days[bloques[0]].date()} → {days[bloques[-1] + SESSIONS].date()}), "
          f"{años:.1f} años · momentum top {a.top} · gastos {COST:.2%}/operación\n")
    print("Cobertura de precios (miembros del índice ese día con datos):")
    for i in bloques[::4]:
        base = miembros(m, days[i])
        con = sum(1 for t in base if t in cols and not np.isnan(A[i, cols[t]]))
        print(f"  {days[i].date()}: {con}/{len(base)}")
    g_spy = [spy.iat[b + SESSIONS] / spy.iat[b] for b in bloques]
    print()
    res = {}
    for tipo in ("hoy", "entonces"):
        g = [semestre(tipo, b) for b in bloques]
        gana = sum(x > y for x, y in zip(g, g_spy))
        cagr = np.prod(g) ** (1 / años) - 1
        peor = min(x - y for x, y in zip(g, g_spy))
        res[tipo] = cagr
        print(f"  {tipo.upper():9s} {cagr * 100:+6.1f} %/año · gana al SPY {gana}/{len(bloques)} semestres · "
              f"peor semestre vs SPY {peor * 100:+6.1f} pts")
    print(f"  {'SPY':9s} {(np.prod(g_spy) ** (1 / años) - 1) * 100:+6.1f} %/año (sin gastos)")
    print(f"\nSesgo de supervivencia medido: {(res['hoy'] - res['entonces']) * 100:+.1f} pts/año (cota inferior si faltan precios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
