"""G1: /dashboard completo y coherente con el mundo; el control de velocidad funciona."""
from __future__ import annotations

import asyncio
import os
import threading
from datetime import timedelta

import requests

from _common import REAL_START, check
os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"
from econosim.run import build_world
from econosim.control import control_app
from aiohttp import web


w, fn = build_world(None, 5000.0, ":memory:", 300.0, episode_seed="EPDASH", years=5)
# montar una tienda y una campaña para poblar el panel
stripe = w.twins["stripe"]
prod = "prod_p"
stripe.products[prod] = {"id": prod, "object": "product", "name": "Kit", "metadata": {"category": "digital_product"},
                         "description": "Kit de plantillas de productividad para autónomos. " * 8, "created": 0, "livemode": True}
pr = stripe._id("price")
stripe.prices[pr] = {"id": pr, "object": "price", "product": prod, "unit_amount": 1500, "currency": "usd", "active": True, "type": "one_time"}
stripe.on_price(stripe.products[prod], stripe.prices[pr])
w.twins["meta_ads"].mgr.create_campaign("c1", "Lanzamiento", "digital_product", 8.0)
w.advance(timedelta(days=90))

# servir el control API en un hilo
loop = asyncio.new_event_loop()
port = {}
ready = threading.Event()
def run():
    asyncio.set_event_loop(loop)
    async def boot():
        runner = web.AppRunner(control_app(w, "", False), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port["p"] = site._server.sockets[0].getsockname()[1]
        ready.set()
    loop.run_until_complete(boot())
    loop.run_forever()
threading.Thread(target=run, daemon=True).start()
ready.wait(5)
U = f"http://127.0.0.1:{port['p']}"

d = requests.get(U + "/dashboard", timeout=5).json()
# campos clave presentes
for k in ("display_now", "balance_cents", "equity_cents", "daily_burn_cents", "days_left",
          "score", "positions", "stripe", "campaigns", "listings", "actions", "brain", "servers", "ledger", "alive"):
    check(k in d, f"falta {k} en /dashboard")

# coherencia
check(d["balance_cents"] == w.balance(), "saldo del panel != mundo")
check(d["daily_burn_cents"] > 0 and d["days_left"] is not None, "burn/días de vida")
# el burn incluye el VPS (~cx23) + la campaña de $8/día
fx = w.load_pricing("fx")["usd_per_eur"]
min_burn = int(round(8.0 * 100 / fx))
check(d["daily_burn_cents"] >= min_burn, f"el burn {d['daily_burn_cents']} no incluye la campaña")
check(len(d["campaigns"]) >= 1 and d["campaigns"][0]["spend_usd"] > 0, "campañas")
check(len(d["listings"]) >= 1 and d["listings"][0]["category"] == "digital_product", "listings")
check(d["stripe"]["charges"] >= 0 and "available" in d["stripe"], "stripe")
check(len(d["servers"]) >= 1 and d["servers"][0]["name"] == "vps-1", "servidores")
check(isinstance(d["ledger"], list) and d["ledger"], "ledger vacío")
check("score" in d["score_detail"] and "expediente" in d["score_detail"], "puntuación")
# el ledger cuadra
running = 0
for e in [x for x in d["ledger"] if x["account"] == "bank"]:
    running += e["amount_cents"]
    check(e["balance_after"] == running, "balance_after inconsistente")

# --- el control de velocidad funciona ----------------------------------------
r = requests.post(U + "/speed", json={"speed": 100}, timeout=5).json()
check(r["speed"] == 100.0 and w.speed == 100.0, "no cambió la velocidad")
requests.post(U + "/speed", json={"speed": 0}, timeout=5)
check(w.speed == 0.0, "no se pudo pausar")

# --- sin fugas: /dashboard no lleva la fecha real (sin debug) -----------------
import json as _j
check(str(REAL_START.year) not in _j.dumps(d), "el dashboard filtra el año real")

print("DASHBOARD API OK")
