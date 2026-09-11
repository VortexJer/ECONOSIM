"""API de control para el humano/panel. Nunca alcanzable desde el sandbox.

Se enlaza a la interfaz de fuera (no a la red interna) y exige un token si se
configura. Con ECONOSIM_DEBUG=1 expone también la fecha real y permite
saltar en el tiempo (tests).
"""
from __future__ import annotations

import os
from datetime import timedelta

from aiohttp import web

from .world import World


def control_app(world: World, token: str = "", debug: bool = False) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth(request: web.Request, handler):
        if token and request.headers.get("X-Econosim-Token") != token:
            return web.json_response({"error": "forbidden"}, status=403)
        return await handler(request)

    app.middlewares.append(auth)

    async def state(_):
        return web.json_response(world.state(include_real=debug))

    async def speed(req):
        body = await req.json()
        world.speed = float(body.get("speed", 0))
        return web.json_response({"speed": world.speed})

    async def advance(req):
        if not debug:
            return web.json_response({"error": "debug only"}, status=403)
        body = await req.json()
        ran = world.advance(timedelta(seconds=float(body.get("seconds", 0))))
        return web.json_response({"events_run": ran, **world.state(include_real=True)})

    async def ledger(_):
        return web.json_response([e.__dict__ for e in world.ledger.entries()])

    async def hetzner(_):
        h = world.twins.get("hetzner")
        if h is None:
            return web.json_response({})
        return web.json_response({
            "locked": h.locked,
            "servers": [{"id": s.id, "name": s.name, "type": s.stype["name"], "status": s.status,
                         "created": h.clock.display_iso(s.created_real)} for s in h.servers.values()],
            "invoices": [{k: v for k, v in i.items() if k != "issued_real"} for i in h.invoices],
        })

    async def fakenet(_):
        fn = world.twins.get("fakenet")
        return web.json_response({"hosts": fn.hosts(), "served": fn.served, "dropped": fn.dropped} if fn else {})

    app.router.add_get("/state", state)
    app.router.add_post("/speed", speed)
    app.router.add_post("/advance", advance)
    app.router.add_get("/ledger", ledger)
    app.router.add_get("/hetzner", hetzner)
    app.router.add_get("/fakenet", fakenet)
    return app


def debug_enabled() -> bool:
    return os.environ.get("ECONOSIM_DEBUG", "") not in ("", "0", "false")
