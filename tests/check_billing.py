"""G3: facturación Hetzner: horas empezadas con tope mensual + IPv4, COBRADA CADA DÍA.

Hetzner factura el día 1 con tope mensual; el sim cobra cada medianoche el incremento del
acumulado del mes con ese mismo tope. Invariante: el TOTAL del mes es idéntico al real,
pero el consumo se ve día a día (si no, el calendario mostraba 29 días 'gratis').
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from _common import ROOT, check, make_world
from econosim.clock import UTC
from econosim.ledger import to_cents
from econosim.twins.hetzner import IPV4_HOURLY, COUNTERPARTY

pricing = json.loads((ROOT / "data" / "pricing" / "hetzner.json").read_text(encoding="utf-8"))
check(pricing["_source"]["url"].startswith("http") and pricing["_source"]["retrieved"], "precios sin fuente/fecha")
types = {t["name"]: t for t in pricing["server_types"]}
cx23, cx33 = types["cx23"], types["cx33"]
ipv4_m = pricing["primary_ipv4_monthly"]


def cost(t: dict, hours: int) -> float:
    return min(hours * t["hourly"], t["monthly"]) + min(hours * IPV4_HOURLY, ipv4_m)


def charges_of(w):
    return [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY]


# --- A: mes completo -> se cobra cada día y la suma es EXACTAMENTE el tope mensual ---
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 10, 2, 0, 0, 1, tzinfo=UTC))
check(len(charges_of(w)) == 1, "debería haber un cargo tras el primer día")
first = charges_of(w)[0]
check(first.amount_cents == -to_cents(cost(cx23, 24)), f"primer día: {first.amount_cents}")
check("1998" not in first.display_ts and first.concept.startswith("Hetzner Cloud uso"), first)
w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))
hours_oct = 31 * 24
check(hours_oct * cx23["hourly"] > cx23["monthly"], "el test no ejercita el tope mensual")
octubre = [c for c in charges_of(w) if c.display_ts[:7] == "2026-10"]
total_oct = -sum(c.amount_cents for c in octubre)
# tolerancia de céntimos por redondear cada día por separado
check(abs(total_oct - to_cents(cost(cx23, hours_oct))) <= len(octubre),
      f"total de octubre {total_oct} != tope mensual {to_cents(cost(cx23, hours_oct))}")
check(len(octubre) >= 20, f"debería cobrarse (casi) cada día, hubo {len(octubre)} cargos")
# con el tope alcanzado, los últimos días del mes salen a 0 (como el tope real)
check(w.balance() == 5000 - total_oct, "saldo tras el mes")
check(any(l == "hetzner:invoice" for _, l in w.clock.pending()), "no hay siguiente cobro programado")

# --- B: parte proporcional cuando NO se llega al tope --------------------------
w, h = make_world(datetime(1998, 10, 20, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))
hours = 12 * 24
check(hours * cx23["hourly"] < cx23["monthly"], "el test no ejercita la parte proporcional")
total = -sum(c.amount_cents for c in charges_of(w))
check(abs(total - to_cents(cost(cx23, hours))) <= 12, f"parcial: {total} != {to_cents(cost(cx23, hours))}")

# --- C: un segundo servidor se cobra por sus horas y desaparece al borrarlo ------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 10, 10, 12, 0, tzinfo=UTC))
s2 = h._create("web-2", cx33, h.images["debian-12"], h.locations["fsn1"], {})
w.advance_to(datetime(1998, 10, 12, 12, 30, tzinfo=UTC))
s2.deleted = True
s2.end_usage(w.clock.real_now())
w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))
h2 = 49                                       # 10/10 12:00 -> 12/10 12:30 = 48.5 h -> 49 empezadas
total = -sum(c.amount_cents for c in charges_of(w))
esperado = to_cents(cost(cx23, 31 * 24)) + to_cents(cost(cx33, h2))
check(abs(total - esperado) <= 40, f"dos servidores: {total} != {esperado}")
check(s2.id not in h.servers and h.own_vps_id in h.servers, "servidor borrado sigue en la lista tras cobrar")

# --- D: dos meses seguidos -> cada mes suma su tope --------------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 12, 1, 0, 0, 1, tzinfo=UTC))
por_mes = {}
for c in charges_of(w):
    por_mes[c.display_ts[:7]] = por_mes.get(c.display_ts[:7], 0) - c.amount_cents
check(set(por_mes) == {"2026-10", "2026-11"}, f"meses cobrados: {sorted(por_mes)}")
check(abs(por_mes["2026-10"] - to_cents(cost(cx23, 31 * 24))) <= 31, f"octubre {por_mes['2026-10']}")
check(abs(por_mes["2026-11"] - to_cents(cost(cx23, 30 * 24))) <= 30, f"noviembre {por_mes['2026-11']}")

print("BILLING OK")
