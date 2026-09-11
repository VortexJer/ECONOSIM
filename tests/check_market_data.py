"""G1: dataset histórico real con fuente, y cargador a series diarias alineadas."""
from __future__ import annotations

import json
from datetime import date

from _common import ROOT, check
from econosim.market.data import MarketData

manifest = json.loads((ROOT / "data" / "market" / "manifest.json").read_text(encoding="utf-8"))
src = manifest["_source"]
check("Yahoo" in src["provider"] and src["retrieved"] and src["start"], "manifest sin fuente/fecha")

md = MarketData()
check(len(md.symbols) >= 10, f"pocos símbolos: {len(md.symbols)}")

# >= 10 años de recorrido total
alldays = md.trading_days()
span_years = (alldays[-1] - alldays[0]).days / 365.25
check(span_years >= 10, f"histórico corto: {span_years:.1f} años")
check(all(sym in manifest["symbols"] for sym in md.symbols), "símbolo sin entrada en el manifest")

# cada serie: ordenada, sin huecos de fin de semana raros, OHLC coherente
for sym in md.symbols:
    s = md.series[sym]
    check(len(s) > 1000, f"{sym}: pocas barras {len(s)}")
    check(s._days == sorted(s._days) and len(set(s._days)) == len(s._days), f"{sym}: fechas desordenadas/duplicadas")
    for b in (s.bars[0], s.bars[len(s) // 2], s.bars[-1]):
        check(b.low <= b.open <= b.high and b.low <= b.close <= b.high and b.low > 0, f"{sym}: OHLC incoherente {b}")
        check(b.day.weekday() < 5, f"{sym}: barra en fin de semana {b.day}")

# indexado por fecha: on() exacto, asof() cae a la anterior si no cotiza
s = md.series[md.symbols[0]]
d0 = s.bars[10].day
check(s.on(d0) is s.bars[10], "on() no devuelve la barra exacta")
# un domingo entre dos sesiones -> asof da la del viernes
mid = s.bars[10].day
while mid.weekday() != 6:            # buscar un domingo cercano
    mid = date.fromordinal(mid.toordinal() + 1)
prev = s.asof(mid)
check(prev is not None and prev.day <= mid and prev.day.weekday() < 5, "asof() no cae a la sesión anterior")
check(s.on(mid) is None, "on() de un domingo debería ser None")

# la unión de días de cotización de dos símbolos contiene los de cada uno
two = md.symbols[:2]
u = set(md.trading_days(two))
check(set(md.series[two[0]]._days) <= u and set(md.series[two[1]]._days) <= u, "unión de días incompleta")

print("MARKET DATA OK")
