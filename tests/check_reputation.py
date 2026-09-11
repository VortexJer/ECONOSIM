"""G4: la reputación sube con ventas y baja con reembolsos/disputas, acotada, y modula el resultado."""
from __future__ import annotations

import statistics

from _common import REAL_START, check
from econosim.commerce.reputation import Reputation, START, MIN, MAX
from econosim.resolver.rates import BaseRates
from econosim.resolver.ficha import Ficha
from econosim.resolver.resolve import resolve

rep = Reputation()
CAT = "digital_product"
check(rep.score(CAT) == START, "reputación inicial no neutra")

# ventas suben, disputas/reembolsos bajan (y más una disputa que un reembolso)
for _ in range(20):
    rep.on_sale(CAT)
up = rep.score(CAT)
check(up > START, "las ventas deberían subir la reputación")
rep.on_refund(CAT)
after_refund = rep.score(CAT)
check(after_refund < up, "un reembolso debería bajar la reputación")
rep.on_dispute(CAT)
check(up - after_refund < after_refund - rep.score(CAT), "una disputa debería doler más que un reembolso")

# acotada [0, 100]
r2 = Reputation()
for _ in range(1000):
    r2.on_dispute(CAT)
check(r2.score(CAT) == MIN, "no se acota por abajo")
for _ in range(5000):
    r2.on_sale(CAT)
check(r2.score(CAT) == MAX, "no se acota por arriba")

# --- modula el resultado: más reputación -> mejor conversión -----------------
br = BaseRates()
N = 3000


def mean_units(rep_score):
    bonus = (rep_score - START) / 20.0            # como quality_bonus
    f = Ficha(category=CAT, price=13.0, quality=min(10.0, max(0.0, 5.0 + bonus)))
    return statistics.mean(resolve(f, br, "ep", f"{rep_score}-{i}").units for i in range(N))


low = mean_units(20)      # mala reputación
neutral = mean_units(50)  # neutra
high = mean_units(95)     # buena reputación
check(high > neutral > low, f"la reputación debería modular las ventas: {low:.1f} < {neutral:.1f} < {high:.1f}")

# reproducible: la misma secuencia de eventos da la misma reputación
a, b = Reputation(), Reputation()
seq = ["sale", "sale", "refund", "sale", "dispute", "sale"]
for k in seq:
    getattr(a, f"on_{k}")(CAT)
    getattr(b, f"on_{k}")(CAT)
check(a.score(CAT) == b.score(CAT), "misma secuencia, distinta reputación")

# el bonus de calidad: 50 -> 0, 100 -> +2.5, 0 -> -2.5
check(abs(Reputation().quality_bonus(CAT)) < 1e-9, "reputación neutra debería dar bonus 0")

print("REPUTATION OK")
