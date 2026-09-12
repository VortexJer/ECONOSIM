"""G1: crear producto+precio en Stripe -> ventas periódicas por el resolutor -> banco."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import REAL_START, LiveApp, check
from econosim.commerce.competition import Competition
from econosim.commerce.market_bridge import MarketBridge, RESOLVE_EVERY_DAYS
from econosim.commerce.reputation import Reputation
from econosim.judge.judge import Judge
from econosim.resolver.engine import ActionResolver
from econosim.resolver.rates import BaseRates
from econosim.twins.stripe import StripeTwin
from econosim.world import World

br = BaseRates()
GOOD = {"completeness": 9, "usefulness": 9, "correctness": 9, "polish": 8, "differentiation": 8}
CONTENT = "Plantilla de Notion completa para autónomos: facturación, gastos, clientes, impuestos. " * 6


def build(seed, judge_dims=GOOD, ads=None):
    w = World(REAL_START, initial_eur=100000.0, episode_id=seed)
    st = StripeTwin(w, secret_key="sk")
    judge = Judge(lambda s, u, m: {"dimensions": judge_dims, "justification": "x"})
    r = ActionResolver(w, br, stripe=st, reputation=Reputation(),
                       competition=Competition(br, w.episode.id), judge=judge, ad_managers=ads or [])
    mb = MarketBridge(w, r, st, judge=judge)
    return w, st, r, mb


# --- crear producto+precio registra un listing y arranca las ventas ----------
w, st, r, mb = build("MB")
H = {"Authorization": "Bearer sk"}
with LiveApp(st.app()) as net:
    U = net.url
    prod = requests.post(U("/v1/products"), headers=H,
                         data={"name": "Plantilla Notion", "description": CONTENT,
                               "metadata[category]": "digital_product"}).json()["id"]
    price = requests.post(U("/v1/prices"), headers=H, data={"product": prod, "unit_amount": "1300"}).json()["id"]
    check(price in mb.listings, "el precio no registró un listing")
    lst = mb.listings[price]
    check(lst["category"] == "digital_product" and lst["price_usd"] == 13.0, lst)
    check(lst["quality"] > 7, f"el juez debería dar calidad alta al buen contenido, dio {lst['quality']}")

    # --- al correr meses, hay ventas por Stripe y payouts al banco ----------
    bank0 = w.balance()
    w.advance(timedelta(days=RESOLVE_EVERY_DAYS * 8 + 40))     # varios ciclos + liquidación
    check(lst["cycles"] >= 4, f"deberían haberse resuelto varios ciclos, {lst['cycles']}")
    check(st.charge_count > 0, "no hubo cobros en Stripe")
    check(len(st.payouts) >= 1 and w.balance() > bank0, "no llegó dinero al banco por las ventas")

# --- sin producto no hay ventas de la nada -----------------------------------
w2, st2, r2, mb2 = build("EMPTY")
bank0 = w2.balance()
w2.advance(timedelta(days=200))
check(st2.charge_count == 0 and w2.balance() == bank0, "sin producto no debería haber ingresos")

# --- un producto peor vende menos que uno bueno (misma semilla, mismo precio) --
POOR = {"completeness": 2, "usefulness": 2, "correctness": 3, "polish": 2, "differentiation": 1}
def units_after(seed, dims):
    w, st, r, mb = build(seed, judge_dims=dims)
    with LiveApp(st.app()) as net:
        U = net.url; H = {"Authorization": "Bearer sk"}
        prod = requests.post(U("/v1/products"), headers=H, data={"name": "P", "description": CONTENT, "metadata[category]": "digital_product"}).json()["id"]
        requests.post(U("/v1/prices"), headers=H, data={"product": prod, "unit_amount": "1300"})
        w.advance(timedelta(days=RESOLVE_EVERY_DAYS * 12 + 10))
        return sum(l["units"] for l in mb.listings.values())
good_u = units_after("Q", GOOD)
poor_u = units_after("Q", POOR)
check(good_u > poor_u, f"mejor calidad debería vender más: bueno {good_u} vs malo {poor_u}")

print("MARKET BRIDGE OK")
