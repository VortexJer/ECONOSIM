"""G5: competencia por nicho fija referencia de precio y saturación; precio vs rivales mueve ventas."""
from __future__ import annotations

import statistics

from _common import REAL_START, check
from econosim.commerce.competition import Competition
from econosim.resolver.rates import BaseRates
from econosim.resolver.ficha import Ficha
from econosim.resolver.resolve import resolve

br = BaseRates()
comp = Competition(br, seed="ep-comp")
CAT = "digital_product"

# --- rivales: existen, con precio y calidad; deterministas por semilla --------
rv = comp.rivals(CAT)
check(4 <= len(rv) <= 12, f"nº de rivales fuera de rango: {len(rv)}")
check(all(r.price > 0 and 0 < r.quality <= 10 for r in rv), "rival con precio/calidad inválidos")
comp2 = Competition(br, seed="ep-comp")
check([r.price for r in comp2.rivals(CAT)] == [r.price for r in rv], "no reproducible con la misma semilla")
comp3 = Competition(br, seed="otra")
check([r.price for r in comp3.rivals(CAT)] != [r.price for r in rv], "otra semilla debería dar otros rivales")

ref = comp.price_reference(CAT)
check(min(r.price for r in rv) <= ref <= max(r.price for r in rv), "la referencia no es la mediana de los rivales")
check(comp.base_saturation(CAT) == len(rv), "saturación base != nº de rivales")

# --- precio por debajo de los rivales vende más; por encima, menos ------------
N = 3000


def mean_units(price):
    f = Ficha(category=CAT, price=price, quality=5.0)
    return statistics.mean(resolve(f, br, "ep", f"{price}-{i}", saturation=0, price_ref=ref).units for i in range(N))


def mean_ref(rates, cat, price, ref):
    f = Ficha(category=cat, price=price, quality=5.0)
    return statistics.mean(resolve(f, rates, "ep", f"{ref}-{price}-{i}", saturation=0, price_ref=ref).units for i in range(N))


cheap = mean_units(ref * 0.6)     # 40% más barato que el nicho
at_ref = mean_units(ref)
pricey = mean_units(ref * 1.8)    # casi el doble
check(cheap > at_ref > pricey, f"elasticidad vs rivales: {cheap:.1f} > {at_ref:.1f} > {pricey:.1f} no se cumple")

# --- más rivales (más saturación) lo ponen más difícil ------------------------
f = Ficha(category=CAT, price=ref, quality=5.0)
few = statistics.mean(resolve(f, br, "ep", f"few-{i}", saturation=4, price_ref=ref).units for i in range(N))
many = statistics.mean(resolve(f, br, "ep", f"many-{i}", saturation=30, price_ref=ref).units for i in range(N))
check(few > many * 1.3, f"más competencia debería reducir ventas: {few:.1f} -> {many:.1f}")

# --- la referencia de precio importa: el MISMO precio vende distinto según el nicho ---
u_low_ref = mean_ref(br, CAT, price=20.0, ref=10.0)   # caro respecto a un nicho barato
u_high_ref = mean_ref(br, CAT, price=20.0, ref=40.0)  # barato respecto a un nicho caro
check(u_high_ref > u_low_ref, f"el mismo precio debería vender más en un nicho caro: {u_low_ref:.1f} vs {u_high_ref:.1f}")

print("COMPETITION OK")
