"""G4: resolver produce eventos futuros coherentes que mueven el ledger al avanzar el reloj."""
from __future__ import annotations

from datetime import timedelta

from _common import REAL_START, check
from econosim.resolver.engine import ActionResolver, COUNTERPARTY
from econosim.resolver.rates import BaseRates
from econosim.resolver.ficha import Ficha
from econosim.resolver.resolve import resolve
from econosim.world import World

br = BaseRates()

# --- una acción con coste inicial + recurrente + ventas: el calendario cuadra ---
w = World(REAL_START, initial_eur=5000.0)      # 5000 EUR para cubrir costes
r = ActionResolver(w, br)
# forzamos un desenlace con ventas: e-commerce (tiene coste inicial 50 y recurrente 29)
# buscamos una semilla de acción que dé unidades > 0 y calidad alta para asegurar ventas
ficha, out = None, None
for i in range(200):
    fa, oa = r.submit("monto una tienda de dropshipping de gadgets a 25$", f"buscar-{i}",
                      quality=9, marketing_reach=8, has_deliverable=True, price=25.0)
    if oa.units > 0 and any(e.kind == "sale" for e in oa.events):
        ficha, out = fa, oa
        break
check(out is not None, "no se encontró un desenlace con ventas en 200 intentos (revisar modulación)")
check(ficha.category == "ecommerce_physical", f"categoría {ficha.category}")

# el coste inicial se cobró YA (day_offset 0)
cat = br["ecommerce_physical"]
initial = cat["initial_cost"]
first_charges = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY and e.amount_cents < 0]
check(any(abs(e.amount_cents) > 0 for e in first_charges), "no se cobró el coste inicial de inmediato")
saldo_tras_inicial = w.balance()
check(saldo_tras_inicial < 500000, "el coste inicial no bajó el saldo")

# hay eventos futuros programados (ventas y costes recurrentes)
pend = [l for _, l in w.clock.pending()]
check(any("resolver:sale" == l for l in pend), "no hay ventas programadas")
check(any("resolver:recurring_cost" == l for l in pend), "no hay coste recurrente programado")

# --- avanzar el reloj hasta el final de la ventana: los ingresos entran -------
before = w.balance()
w.advance(timedelta(days=cat["sales_window_days"] + 35))
sales = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY and e.amount_cents > 0 and e.ref.startswith("buscar")]
recur = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY and e.amount_cents < 0 and "recurrente" in e.concept]
check(len(sales) >= 1, "no llegaron ventas al avanzar el reloj")
check(len(recur) >= 1, "no se cobró ningún coste recurrente")
# el ingreso total de ventas ≈ unidades * precio / fx (en céntimos)
fx = w.load_pricing("fx")["usd_per_eur"]
from econosim.ledger import to_cents
expected_rev = to_cents(out.revenue_usd / fx)
got_rev = sum(e.amount_cents for e in sales)
check(abs(got_rev - expected_rev) <= 2 * len(sales) + 2, f"ingresos {got_rev} != esperado {expected_rev}")

# --- un producto digital sin coste inicial: solo ventas, cero costes fijos ----
w2 = World(REAL_START, initial_eur=100.0)
r2 = ActionResolver(w2, br)
f2, o2 = r2.submit("vendo un ebook en PDF a 10$", "eb-1", quality=5, price=10.0)
check(f2.category == "digital_product", f2.category)
check(all(e.kind != "recurring_cost" and e.kind != "initial_cost" for e in o2.events),
      "el producto digital no debería tener costes fijos")

# --- una apuesta: EV negativo, un único evento de resultado -------------------
w3 = World(REAL_START, initial_eur=1000.0)
r3 = ActionResolver(w3, br)
pnls = []
for i in range(300):
    _, o = r3.submit("apuesto 10$ al partido", f"bet-{i}", price=10.0)
    check(len(o.events) == 1 and o.events[0].kind == "bet", "la apuesta debe tener un solo evento")
    pnls.append(o.revenue_usd)
avg = sum(pnls) / len(pnls)
check(avg < 0, f"la apuesta debería tener EV negativo, media {avg:.2f}")

print("RESOLVE OK")
