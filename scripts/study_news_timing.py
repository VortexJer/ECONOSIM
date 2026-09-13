"""¿El periódico informa TARDE o PRONTO? Medición con datos reales.

Yahoo solo sirve titulares de hoy, así que se mide sobre el evento fechado que un diario
financiero publica siempre: los RESULTADOS trimestrales (histórico disponible).

Dos preguntas, dos medidas:
  1) ¿Dónde ocurre el movimiento? |retorno| medio en t-3..t+5 alrededor del anuncio.
     Si el pico está en t0/t+1, cuando el papel del día siguiente lo cuenta ya pasó.
  2) ¿Queda algo que explotar después? Deriva con signo (PEAD): condicionada a la
     dirección del día del anuncio, cuánto se mueve DESPUÉS en esa misma dirección.
     Si es > 0, un lector del papel del día siguiente aún tiene una ventaja pequeña.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

import pandas as pd
import yfinance as yf

TICKERS = ["AAPL", "AMZN", "BA", "BAC", "CAT", "CVX", "DIS", "JPM", "KO", "MSFT",
           "NVDA", "PFE", "T", "WMT", "XOM"]
OFFSETS = list(range(-3, 6))          # t-3 .. t+5


def main() -> None:
    absr = defaultdict(list)          # desfase -> |retorno %|
    drift = defaultdict(list)         # desfase -> retorno % alineado con el signo de t0
    n_ev = 0
    for t in TICKERS:
        try:
            tk = yf.Ticker(t)
            ed = tk.get_earnings_dates(limit=40)
            px = tk.history(period="5y", interval="1d", auto_adjust=True)
        except Exception as e:
            print(f"  {t}: fallo ({type(e).__name__})", file=sys.stderr); continue
        if ed is None or ed.empty or px.empty:
            print(f"  {t}: sin datos"); continue
        ret = (px["Close"].pct_change() * 100).dropna()
        days = [d.date() for d in ret.index]
        pos = {d: i for i, d in enumerate(days)}
        used = 0
        for ts in ed.index:
            day = ts.date()
            i = pos.get(day)
            if i is None:                       # anuncio fuera de sesión: primer día hábil siguiente
                nxt = [j for j, dd in enumerate(days) if dd >= day]
                if not nxt:
                    continue
                i = nxt[0]
            if not all(0 <= i + o < len(ret) for o in OFFSETS):
                continue
            r0 = float(ret.iloc[i])
            if r0 == 0:
                continue
            sign = 1.0 if r0 > 0 else -1.0
            for o in OFFSETS:
                v = float(ret.iloc[i + o])
                absr[o].append(abs(v))
                drift[o].append(v * sign)       # alineado con la dirección del día del anuncio
            used += 1
        n_ev += used
        print(f"  {t}: {used} anuncios de resultados", flush=True)
        time.sleep(0.3)

    if not n_ev:
        raise SystemExit("no se pudo medir ningún anuncio")

    print(f"\n=== {n_ev} anuncios de resultados · |retorno| medio por día ===")
    base = sum(absr[-3] + absr[-2]) / len(absr[-3] + absr[-2])       # día "normal" de referencia
    for o in OFFSETS:
        v = absr[o]
        marca = "  <-- ANUNCIO" if o == 0 else ("  <-- el papel del día siguiente" if o == 1 else "")
        print(f"  t{o:+d}: {sum(v)/len(v):5.2f}%  (x{(sum(v)/len(v))/base:4.2f} vs día normal){marca}")

    print(f"\n=== ¿queda deriva explotable? (retorno alineado con la dirección del anuncio) ===")
    acum = 0.0
    for o in range(1, 6):
        m = sum(drift[o]) / len(drift[o])
        acum += m
        print(f"  t+{o}: {m:+.3f}%   acumulado {acum:+.3f}%")
    m0 = sum(drift[0]) / len(drift[0])
    print(f"\n  salto del día del anuncio: {m0:+.2f}%")
    print(f"  deriva posterior (t+1..t+5): {acum:+.3f}%  =  {100*acum/m0:.1f}% del salto")
    json.dump({"eventos": n_ev, "abs": {str(o): sum(absr[o])/len(absr[o]) for o in OFFSETS},
               "dia_normal": base, "salto_t0": m0,
               "deriva": {str(o): sum(drift[o])/len(drift[o]) for o in range(1, 6)},
               "deriva_acumulada": acum},
              open("scripts/news_timing.json", "w", encoding="utf-8"), indent=1)
    print("  guardado en scripts/news_timing.json")


if __name__ == "__main__":
    main()
