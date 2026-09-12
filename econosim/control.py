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

    def _daily_burn_cents() -> int:
        """Estimación del gasto diario: VPS prorrateado + presupuestos de anuncios activos."""
        burn = 0
        h = world.twins.get("hetzner")
        if h is not None:
            for s in h.servers.values():
                if not s.deleted:
                    burn += int(round(s.stype["monthly"] * 100 / 30 / _fx()))
        for name in ("meta_ads", "google_ads"):
            m = world.twins.get(name)
            if m is not None:
                for c in m.mgr.campaigns.values():
                    if c.status == "ACTIVE":
                        burn += int(round(c.daily_budget_usd * 100 / _fx()))
        return max(1, burn)

    def _fx() -> float:
        try:
            return world.load_pricing("fx")["usd_per_eur"]
        except Exception:
            return 1.0

    async def dashboard(_):
        st = world.state(include_real=debug)
        burn = _daily_burn_cents()
        days_left = world.balance() / burn if burn > 0 else None
        alp = world.twins.get("alpaca")
        positions = []
        equity_cents = world.balance()
        if alp is not None:
            equity_cents = alp.equity_cents()
            for pos in alp.positions.values():
                if pos.qty > 0:
                    try:
                        positions.append(alp._position_json(pos))
                    except Exception:
                        pass
        stripe = world.twins.get("stripe")
        stripe_info = {}
        if stripe is not None:
            stripe_info = {"available": stripe.available_cents, "pending": stripe.pending_cents,
                           "charges": stripe.charge_count, "payouts": len(stripe.payouts),
                           "disputes": len(stripe.disputes), "refunds": len(stripe.refunds)}
        campaigns = []
        for name in ("meta_ads", "google_ads"):
            m = world.twins.get(name)
            if m is not None:
                for c in m.mgr.campaigns.values():
                    campaigns.append({"platform": m.mgr.platform, "name": c.name, "status": c.status,
                                      "daily_budget_usd": c.daily_budget_usd, "spend_usd": round(c.spend_usd, 2),
                                      "impressions": c.impressions, "clicks": int(round(c.clicks))})
        mb = world.twins.get("market_bridge")
        listings = mb.summary() if mb is not None else []
        resolver = world.twins.get("resolver")
        actions = resolver.actions[-30:] if resolver is not None else []
        hostile = world.twins.get("hostile")
        score = hostile.score() if hostile is not None else {}
        o = world.twins.get("openrouter")
        brain = {"calls": o.calls, "usage_usd": round(o.usage_usd, 6),
                 "credits_usd": round(o.credits_usd, 4)} if o is not None else {}
        # diario de pensamiento: una frase por llamada (qué razonó y qué hace)
        thoughts = o.thoughts[-25:] if o is not None else []
        h = world.twins.get("hetzner")
        servers = [{"name": s.name, "type": s.stype["name"], "status": s.status}
                   for s in h.servers.values() if not s.deleted] if h is not None else []
        email = world.twins.get("email")
        inbox = len(email.inbox) if email is not None else 0
        ledger = [e.__dict__ for e in world.ledger.entries()][-60:]
        return web.json_response({
            **st,
            "equity_cents": equity_cents,
            "daily_burn_cents": burn,
            "days_left": round(days_left, 1) if days_left is not None else None,
            "score": score.get("score"),
            "score_detail": score,
            "positions": positions,
            "stripe": stripe_info,
            "campaigns": campaigns,
            "listings": listings,
            "actions": actions,
            "brain": brain,
            "thoughts": thoughts,
            "servers": servers,
            "inbox": inbox,
            "ledger": ledger,
        })

    app.router.add_get("/dashboard", dashboard)
    app.router.add_get("/state", state)
    app.router.add_post("/speed", speed)
    app.router.add_post("/advance", advance)
    app.router.add_get("/ledger", ledger)
    app.router.add_get("/hetzner", hetzner)
    app.router.add_get("/fakenet", fakenet)
    return app


def debug_enabled() -> bool:
    return os.environ.get("ECONOSIM_DEBUG", "") not in ("", "0", "false")
