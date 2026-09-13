"""¿El periódico informa TARDE o PRONTO? Medición con datos reales.

Para cada titular fechado de una acción, mira el retorno de esa acción en los días
alrededor de la publicación (t-3..t+3). Si el movimiento gordo está ANTES o EN t0,
el papel cuenta lo que ya pasó (no da ventaja). Si está DESPUÉS, hay ventaja explotable.

Salida: retorno absoluto medio por desfase, y qué fracción del movimiento total de la
ventana ocurre antes vs después de publicarse.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import yfinance as yf

TICKERS = ["AAPL", "AMZN", "BA", "BAC", "CAT", "CVX", "DIS", "JPM", "KO", "MSFT",
           "NVDA", "PFE", "T", "WMT", "XOM"]
OFFSETS = [-3, -2, -1, 0, 1, 2, 3]


def news_dates(t: str) -> list[datetime]:
    """Fechas (UTC) de los titulares que expone Yahoo para ese ticker."""
    out = []
    try:
        items = yf.Ticker(t).news or []
    except Exception as e:
        print(f"  {t}: sin noticias ({type(e).__name__})", file=sys.stderr)
        return out
    for it in items:
        ts = it.get("providerPublishTime")
        if ts is None:                      # yfinance 1.x anida en 'content'
            c = it.get("content") or {}
            pub = c.get("pubDate") or c.get("displayTime")
            if pub:
                try:
                    out.append(datetime.fromisoformat(str(pub).replace("Z", "+00:00")).astimezone(timezone.utc))
                except ValueError:
                    pass
            continue
        out.append(datetime.fromtimestamp(int(ts), tz=timezone.utc))
    return out


def main() -> None:
    by_offset = defaultdict(list)          # desfase -> [retorno absoluto %]
    signed = defaultdict(list)
    n_headlines = 0
    for t in TICKERS:
        dates = news_dates(t)
        if not dates:
            continue
        try:
            px = yf.Ticker(t).history(period="6mo", interval="1d", auto_adjust=True)
        except Exception:
            continue
        if px.empty:
            continue
        ret = px["Close"].pct_change() * 100
        idx = [d.date() for d in ret.index]
        pos = {d: i for i, d in enumerate(idx)}
        used = 0
        for d in dates:
            day = d.date()
            # el día de cotización igual o siguiente a la publicación
            i = pos.get(day)
            if i is None:
                cand = [j for j, dd in enumerate(idx) if dd >= day]
                if not cand:
                    continue
                i = cand[0]
            ok = all(0 <= i + o < len(ret) for o in OFFSETS)
            if not ok:
                continue
            for o in OFFSETS:
                v = ret.iloc[i + o]
                if v == v:                  # no NaN
                    by_offset[o].append(abs(float(v)))
                    signed[o].append(float(v))
            used += 1
        n_headlines += used
        print(f"  {t}: {used} titulares emparejados con precios", flush=True)
        time.sleep(0.4)

    if not n_headlines:
        raise SystemExit("no se pudo emparejar ningún titular con precios")

    print(f"\n=== {n_headlines} titulares fechados, movimiento medio |retorno| por desfase ===")
    for o in OFFSETS:
        v = by_offset[o]
        if v:
            marca = "  <-- día del titular" if o == 0 else ""
            print(f"  t{o:+d}: {sum(v)/len(v):5.2f}% (n={len(v)}){marca}")
    antes = [x for o in (-3, -2, -1) for x in by_offset[o]]
    dia = by_offset[0]
    despues = [x for o in (1, 2, 3) for x in by_offset[o]]
    ma, md, mp = (sum(antes)/len(antes), sum(dia)/len(dia), sum(despues)/len(despues))
    print(f"\n  ANTES (t-3..t-1): {ma:.2f}%   DÍA (t0): {md:.2f}%   DESPUÉS (t+1..t+3): {mp:.2f}%")
    print(f"  ratio despues/antes = {mp/ma:.2f}  (>1 = el papel adelanta; <1 = el papel llega tarde)")
    json.dump({"n": n_headlines, "abs_por_desfase": {str(o): (sum(by_offset[o])/len(by_offset[o])) for o in OFFSETS if by_offset[o]},
               "antes": ma, "dia": md, "despues": mp},
              open("scripts/news_timing.json", "w", encoding="utf-8"), indent=1)
    print("\n  guardado en scripts/news_timing.json")


if __name__ == "__main__":
    main()
