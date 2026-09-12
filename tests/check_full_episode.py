"""G2: episodio de punta a punta en proceso — vive, monta negocio, vende, y termina con puntuación."""
from __future__ import annotations

import os
from datetime import timedelta

import requests

from _common import ROOT, check
import sys
sys.path.insert(0, str(ROOT))
os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"      # el juez/cerebro no dependen del proveedor real
from econosim.run import build_world
from econosim.fakenet.server import FakeNet
from econosim.ledger import Ledger


def ledger_consistent(w) -> bool:
    # el saldo del banco = suma exacta de asientos, y cada balance_after cuadra
    entries = w.ledger.entries("bank")
    running = 0
    for e in entries:
        running += e.amount_cents
        if e.balance_after != running:
            return False
    return running == w.balance()


# ============================================================================
# ESCENARIO A: negocio bueno + saldo amplio -> sobrevive al horizonte con ventas
# ============================================================================
w, fn = build_world(None, 5000.0, ":memory:", 300.0, episode_seed="EPFULL", years=5)
stripe = w.twins["stripe"]
meta = w.twins["meta_ads"]
hostile = w.twins["hostile"]

# la "IA" actúa por las APIs internas de los gemelos (equivale a llamar a las APIs):
# 1) crea un producto con contenido y precio
prod = "prod_x"
stripe.products[prod] = {"id": prod, "object": "product", "name": "Kit de plantillas",
                         "description": "Kit completo de plantillas de productividad para autónomos. " * 8,
                         "metadata": {"category": "digital_product"}, "created": 0, "livemode": True}
price = stripe._id("price")
stripe.prices[price] = {"id": price, "object": "price", "product": prod, "unit_amount": 1500,
                        "currency": "usd", "active": True, "type": "one_time"}
stripe.on_price(stripe.products[prod], stripe.prices[price])     # dispara el listing
# 2) monta una campaña de anuncios
meta.mgr.create_campaign("cmp1", "Lanzamiento", "digital_product", 8.0)

check(ledger_consistent(w), "ledger inconsistente al arrancar")

# correr ~7 meses simulados
for _ in range(7):
    w.advance(timedelta(days=30))
    check(ledger_consistent(w), "el ledger dejó de cuadrar durante el episodio")

# hubo actividad económica real
check(stripe.charge_count > 0, "no hubo ninguna venta")
ad_spend = [e for e in w.ledger.entries() if e.counterparty == "Meta Platforms, Inc."]
check(len(ad_spend) >= 30, "no se gastó en anuncios a diario")
vps = [e for e in w.ledger.entries() if e.counterparty == "Hetzner Online GmbH"]
check(len(vps) >= 1, "no se cobró el VPS")
mb = w.twins["market_bridge"]
check(sum(l["units"] for l in mb.listings.values()) >= 0, "el puente no resolvió")

# el episodio tiene una puntuación calculable
score = hostile.score()
check("score" in score and "balance_eur" in score, "sin puntuación")
check(w.alive, "con 5000 EUR y buen negocio debería sobrevivir 7 meses")

# ============================================================================
# ESCENARIO B: sin negocio y poco saldo -> muere por impago del VPS
# ============================================================================
w2, fn2 = build_world(None, 6.0, ":memory:", 300.0, episode_seed="EPDIE", years=5)  # 6 EUR: no cubre ni un mes
deaths = []
w2.on_death.append(deaths.append)
w2.advance(timedelta(days=80))          # cruza la primera factura + gracia
check(not w2.alive, "con 6 EUR y sin ingresos debería morir por impago del VPS")
check(w2.episode.death_cause == "hosting_unpaid", f"causa de muerte {w2.episode.death_cause}")
score2 = w2.twins["hostile"].score()
check(score2["score"] <= 6.0, "un episodio muerto no debería puntuar alto")

print("FULL EPISODE OK")
