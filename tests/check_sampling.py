"""G3: muestreo de cola pesada, reproducible, media empírica ≈ esperada, y modulación correcta."""
from __future__ import annotations

import statistics

from _common import check
from econosim.resolver.ficha import Ficha
from econosim.resolver.rates import BaseRates
from econosim.resolver.resolve import expected_units, resolve

br = BaseRates()
N = 4000


def units_sample(category, seed_prefix, **ficha_kw):
    f = Ficha(category=category, **ficha_kw)
    return [resolve(f, br, "ep", f"{seed_prefix}-{i}").units for i in range(N)]


# --- digital_product: forma de cola pesada -----------------------------------
f = Ficha(category="digital_product", price=13.0)
us = units_sample("digital_product", "dp", price=13.0)
zeros = sum(1 for u in us if u == 0) / N
check(0.35 < zeros < 0.55, f"fracción de ceros {zeros:.2f} lejos del p_zero de la tabla (0.44)")
med = statistics.median(us)
mean = statistics.mean(us)
check(mean > med, f"la media {mean:.1f} debería superar a la mediana {med} (cola a la derecha)")
# la mayoría rinde poco: el percentil 80 es modesto frente al máximo
us_sorted = sorted(us)
p80 = us_sorted[int(0.8 * N)]
top = us_sorted[-1]
check(top > 5 * max(1, p80), f"sin cola: máx {top} no supera 5× el p80 {p80}")
# unos pocos lo petan: existe una cola larga por encima de 5× la mediana
petan = sum(1 for u in us if u > 5 * max(1, med)) / N
check(0.001 < petan < 0.15, f"cola de éxitos rara pero presente: {petan:.3f}")

# --- media empírica ≈ esperada por la tabla ----------------------------------
exp = expected_units(br, f)
check(abs(mean - exp) / exp < 0.2, f"media empírica {mean:.1f} lejos de la esperada {exp:.1f}")

# --- reproducible: misma semilla+id -> mismo resultado ------------------------
a = resolve(f, br, "epX", "act-7")
b = resolve(f, br, "epX", "act-7")
check(a.units == b.units and a.revenue_usd == b.revenue_usd, "no reproducible")
c = resolve(f, br, "epY", "act-7")
diff = sum(1 for i in range(200) if resolve(f, br, "epX", str(i)).units != resolve(f, br, "epY", str(i)).units)
check(diff > 100, "semillas distintas deberían dar resultados distintos")

# --- modulación: calidad, precio, marketing desplazan en la dirección correcta ----
base_mean = statistics.mean(units_sample("digital_product", "q5", price=13.0, quality=5))
hi_q = statistics.mean(units_sample("digital_product", "q9", price=13.0, quality=9))
lo_q = statistics.mean(units_sample("digital_product", "q1", price=13.0, quality=1))
check(hi_q > base_mean > lo_q, f"calidad: {lo_q:.1f} < {base_mean:.1f} < {hi_q:.1f} no se cumple")

cheap = statistics.mean(units_sample("digital_product", "cheap", price=5.0))
pricey = statistics.mean(units_sample("digital_product", "pricey", price=40.0))
check(cheap > pricey, f"elasticidad: barato {cheap:.1f} debería vender más que caro {pricey:.1f}")

no_mkt = statistics.mean(units_sample("digital_product", "m0", price=13.0, marketing_reach=0))
with_mkt = statistics.mean(units_sample("digital_product", "m5", price=13.0, marketing_reach=5))
check(with_mkt > no_mkt * 1.3, f"marketing debería subir ventas: {no_mkt:.1f} -> {with_mkt:.1f}")

# --- saturación: más competencia en el nicho reduce el resultado -------------
sat0 = statistics.mean([resolve(f, br, "ep", f"s0-{i}", saturation=0).units for i in range(N)])
sat20 = statistics.mean([resolve(f, br, "ep", f"s20-{i}", saturation=20).units for i in range(N)])
check(sat20 < sat0 * 0.8, f"saturación debería reducir ventas: {sat0:.1f} -> {sat20:.1f}")

print("SAMPLING OK")
