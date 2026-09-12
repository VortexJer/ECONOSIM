"""G2: el /dashboard expone el modo en vivo y el diario de egreso; fuera de vivo no."""
from __future__ import annotations

import asyncio
import os
import threading

import requests

from _common import LiveApp, check
os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"
from econosim.run import build_world
from econosim.control import control_app
from aiohttp import web


def boot_control(w):
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
    return f"http://127.0.0.1:{port['p']}"


# --- mundo en vivo: una acción bloqueada debe aparecer en el dashboard -------
w, _ = build_world(None, 5000.0, ":memory:", 300.0, live=True)
alp = w.twins["alpaca"]
with LiveApp(alp.app()) as srv:
    requests.post(srv.url("/v2/orders"),
                  headers={"APCA-API-KEY-ID": alp.key_id, "APCA-API-SECRET-KEY": alp.secret},
                  json={"symbol": "AAPL", "side": "buy", "qty": 2, "type": "market"}, timeout=5)

U = boot_control(w)
d = requests.get(U + "/dashboard", timeout=5).json()
check("live" in d, "el dashboard no expone el bloque 'live'")
live = d["live"]
check(live["enabled"] is True, "el dashboard debería marcar el modo en vivo activo")
check(live["blocked"] == 1, f"el dashboard debería contar 1 acción bloqueada, {live['blocked']}")
check(isinstance(live["journal"], list) and live["journal"], "falta el diario de egreso")
j = live["journal"][-1]
check(j["service"] == "alpaca" and j["op"] == "place_order", "la entrada de egreso no es la orden bloqueada")
check(j["detail"].get("symbol") == "AAPL", "el detalle del egreso no lleva el símbolo")
check(d["balance_cents"] == w.balance(), "el saldo del dashboard no cuadra")

# --- mundo normal: el dashboard marca el modo en vivo APAGADO ----------------
w2, _ = build_world(None, 5000.0, ":memory:", 300.0, live=False)
U2 = boot_control(w2)
d2 = requests.get(U2 + "/dashboard", timeout=5).json()
check(d2["live"]["enabled"] is False, "fuera de vivo el dashboard debe marcar el candado apagado")
check(d2["live"]["blocked"] == 0, "fuera de vivo no hay nada bloqueado")

print("LIVE DASHBOARD OK")
