"""G5: reproducibilidad por semilla y efecto de la saturación del nicho."""
from __future__ import annotations

import statistics

from _common import REAL_START, check
from econosim.resolver.engine import ActionResolver
from econosim.resolver.rates import BaseRates
from econosim.resolver.ficha import Ficha
from econosim.resolver.resolve import resolve
from econosim.world import World

br = BaseRates()

# --- dos acciones idénticas, misma semilla de episodio -> mismo desenlace -----
w1 = World(REAL_START, initial_eur=5000.0, episode_id="EP-FIJO")
r1 = ActionResolver(w1, br)
f1, o1 = r1.submit("vendo una plantilla a 12$", "act-A", quality=6, price=12.0)

w2 = World(REAL_START, initial_eur=5000.0, episode_id="EP-FIJO")
r2 = ActionResolver(w2, br)
f2, o2 = r2.submit("vendo una plantilla a 12$", "act-A", quality=6, price=12.0)

check(o1.units == o2.units and o1.revenue_usd == o2.revenue_usd, "mismo episodio+id no reproduce")
check([e.amount_usd for e in o1.events] == [e.amount_usd for e in o2.events], "eventos no reproducibles")

# --- episodios distintos -> desenlaces distintos ------------------------------
fichax = Ficha(category="digital_product", price=12.0, quality=6)
diff = sum(1 for i in range(300)
           if resolve(fichax, br, "EP-A", str(i)).units != resolve(fichax, br, "EP-B", str(i)).units)
check(diff > 150, f"episodios distintos deberían variar el desenlace; solo {diff}/300")

# --- saturación: al acumularse acciones vivas en la misma categoría, cae el resultado ---
# medimos la media de ventas de las primeras N acciones vs las últimas N (más saturadas)
w = World(REAL_START, initial_eur=1e9, episode_id="EP-SAT")
r = ActionResolver(w, br)
first, last = [], []
K = 400
for i in range(K):
    _, o = r.submit("vendo una plantilla a 12$", f"sat-{i}", quality=6, price=12.0)
    (first if i < 60 else last if i >= K - 60 else []).append(o.units)
check(r.live_by_category["digital_product"] == K, "la saturación no se contabiliza")
check(statistics.mean(first) > statistics.mean(last) * 1.3,
      f"la saturación creciente debería reducir ventas: {statistics.mean(first):.1f} -> {statistics.mean(last):.1f}")

# la saturación es explícita en resolve() y monótona
means = []
for sat in (0, 5, 15, 40):
    means.append(statistics.mean(resolve(fichax, br, "EP", f"{sat}-{i}", saturation=sat).units for i in range(1500)))
check(means == sorted(means, reverse=True), f"la saturación debería reducir monótonamente: {[round(m,1) for m in means]}")

print("DETERMINISM OK")
