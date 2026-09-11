"""G3: facturación Hetzner como la real: horas empezadas con tope mensual + IPv4, factura el día 1."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

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


# --- A: mes completo → tope mensual + IPv4, un único cargo --------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 10, 31, 23, 59, 59, tzinfo=UTC))
check(len(w.ledger.entries()) == 1, "cargo antes de fin de mes")
w.advance(timedelta(seconds=2))
charges = [e for e in w.ledger.entries() if e.amount_cents < 0]
check(len(charges) == 1, f"{len(charges)} cargos, esperado 1")
hours_oct = 31 * 24
check(hours_oct * cx23["hourly"] > cx23["monthly"], "el test no ejercita el tope mensual")
expected = to_cents(cost(cx23, hours_oct))
check(charges[0].amount_cents == -expected, f"cargo {charges[0].amount_cents} != {-expected}")
check(charges[0].counterparty == COUNTERPARTY and "1998" not in charges[0].display_ts, charges[0])
check(w.balance() == 5000 - expected, "saldo tras factura")
inv = h.invoices[-1]
check(inv["paid"] and inv["period"] == "2026-10" and inv["lines"][0]["hours"] == hours_oct, inv)
check(any(l == "hetzner:invoice" for _, l in w.clock.pending()), "no hay siguiente factura programada")
nxt = [t for t, l in w.clock.pending() if l == "hetzner:invoice"][0]
check(nxt == datetime(1998, 12, 1, tzinfo=UTC), f"siguiente factura en {nxt}")

# --- B: semana parcial → por horas, sin tope ----------------------------------
w, h = make_world(datetime(1998, 10, 25, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))
charges = [e for e in w.ledger.entries() if e.amount_cents < 0]
hours = 7 * 24
check(hours * cx23["hourly"] < cx23["monthly"], "el test no ejercita la parte proporcional")
expected = to_cents(cost(cx23, hours))
check(len(charges) == 1 and charges[0].amount_cents == -expected, f"parcial: {charges} != {-expected}")

# --- C: segundo servidor 2 días y media hora → 49 horas empezadas, y desaparece tras facturar
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 10, 10, 12, 0, tzinfo=UTC))
s2 = h._create("worker", cx33, h.images["debian-12"], h.locations["fsn1"], {})
w.advance_to(datetime(1998, 10, 12, 12, 30, tzinfo=UTC))
s2.end_usage(w.clock.real_now())
s2.deleted = True
w.advance_to(datetime(1998, 11, 1, 0, 0, 1, tzinfo=UTC))
charges = [e for e in w.ledger.entries() if e.amount_cents < 0]
h2 = math.ceil(48.5)
expected = to_cents(cost(cx23, 31 * 24) + cost(cx33, h2))
check(len(charges) == 1 and charges[0].amount_cents == -expected, f"dos servidores: {charges} != {-expected}")
check([l["hours"] for l in h.invoices[-1]["lines"]] == [31 * 24, h2], h.invoices[-1]["lines"])
check(s2.id not in h.servers and h.own_vps_id in h.servers, "servidor borrado sigue en la lista tras facturar")

# --- D: dos meses seguidos → dos cargos, uno por mes ---------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.advance_to(datetime(1998, 12, 1, 0, 0, 1, tzinfo=UTC))
charges = [e for e in w.ledger.entries() if e.amount_cents < 0]
check([e.ref for e in charges] == ["INV-2026-10", "INV-2026-11"], [e.ref for e in charges])
check(charges[1].amount_cents == -to_cents(cost(cx23, 30 * 24)), "noviembre")
check(w.alive, "murió pagando 12 EUR de 50")

print("BILLING OK")
