"""G3: arranque aleatorio con margen suficiente, reproducible y variado por semilla."""
from __future__ import annotations

from datetime import timedelta

from _common import check
from econosim.market.data import MarketData
from econosim.market.episode import make_mask, pick_start

md = MarketData()
last = md.trading_days()[-1]

# --- margen: siempre quedan >= N años por delante ------------------------------------
for years in (1, 3, 5, 10):
    horizon = timedelta(days=int(round(years * 365.25)))
    starts = {pick_start(md, f"seed-{i}", years) for i in range(50)}
    for d in starts:
        check(d + horizon <= last, f"arranque {d} sin {years} años de margen (fin {last})")
    # arranca en un día de cotización real
    days = set(md.trading_days())
    check(all(d in days for d in starts), "arranque en día sin cotización")

# --- reproducible: misma semilla -> mismo punto y mismos alias -----------------------
a = make_mask(md, "fijo", 5)
b = make_mask(md, "fijo", 5)
check(a.start_day == b.start_day and a.alias_of == b.alias_of, "no reproducible")

# --- variado: semillas distintas -> puntos y renombrados distintos -------------------
puntos = {pick_start(md, f"s{i}", 5) for i in range(30)}
check(len(puntos) >= 10, f"poca variedad de arranques: {len(puntos)}")
m0, m1 = make_mask(md, "uno", 5), make_mask(md, "dos", 5)
check(m0.start_day != m1.start_day or m0.alias_of != m1.alias_of, "dos semillas dieron el mismo episodio")

# --- pedir más años de los que hay -> error claro ------------------------------------
span = (last - md.trading_days()[0]).days / 365.25
try:
    pick_start(md, "x", span + 5)
    check(False, "debería fallar al pedir más años de los que hay")
except ValueError:
    pass

# el mismo día de arranque cae en regímenes distintos según la semilla (variedad de época)
years_of_start = {make_mask(md, f"r{i}", 5).start_day.year for i in range(40)}
check(len(years_of_start) >= 5, f"los arranques no cubren varias épocas: {sorted(years_of_start)}")

print("START OK")
