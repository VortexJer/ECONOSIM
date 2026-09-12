"""Gemelo de una casa de apuestas (estilo the-odds-api / bookmaker).

Eventos con cuotas que incorporan el margen real (overround > 100%). La IA apuesta
un importe a un resultado; se cobra la apuesta y se liquida tras el evento. Las
cuotas llevan el margen, así que el EV del apostante es negativo a la larga.

Auth: Authorization: Bearer <api-key>. Eventos y resultados deterministas por
semilla de episodio (reproducibles), con probabilidades verdaderas ocultas.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from aiohttp import web

from ..ledger import to_cents
from ..world import World

HOST = "api.the-odds-api.com"
COUNTERPARTY = "Bookmaker Ltd."


def _err(status: int, message: str) -> web.Response:
    return web.json_response({"message": message}, status=status)


class BettingTwin:
    host = HOST

    def __init__(self, world: World, api_key: Optional[str] = None, n_events: int = 12):
        self.world = world
        self.cfg = world.load_pricing("services")["betting"]
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.margin = self.cfg["house_margin"]
        self.api_key = api_key or secrets.token_hex(16)
        self.bets: dict[str, dict] = {}
        self.events = self._make_events(n_events)
        world.register("betting", self)

    def _rng(self, salt: str):
        import random
        h = hashlib.sha256(f"bet:{self.world.episode.id}:{salt}".encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def _make_events(self, n: int) -> dict:
        rng = self._rng("events")
        teams = ["Halcones", "Lobos", "Titanes", "Dragones", "Furia", "Rayo", "Cometas", "Osos",
                 "Águilas", "Tiburones", "Panteras", "Centellas"]
        events = {}
        for i in range(n):
            eid = f"evt_{i:03d}"
            a, b = rng.sample(teams, 2)
            p_home = rng.uniform(0.3, 0.7)      # probabilidad VERDADERA (oculta)
            # cuotas con margen: cuota_justa = 1/p; cuota_con_margen = cuota_justa / (1+margen)
            odd_home = round(1.0 / p_home / (1 + self.margin), 2)
            odd_away = round(1.0 / (1 - p_home) / (1 + self.margin), 2)
            commence = self.world.clock.real_now() + timedelta(days=rng.randint(1, 5))
            events[eid] = {"id": eid, "home": a, "away": b, "p_home": p_home,
                           "odd_home": odd_home, "odd_away": odd_away,
                           "commence_real": commence, "settled": False, "result": None}
        return events

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_get("/v4/sports/{sport}/odds", self.h_odds)
        r.add_get("/v4/sports", self.h_sports)
        r.add_post("/v4/bets", self.h_bet)             # extensión del simulador para apostar
        r.add_get("/v4/bets/{id}", self.h_get_bet)
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        key = request.query.get("apiKey") or (request.headers.get("Authorization", "")[7:]
                                              if request.headers.get("Authorization", "").startswith("Bearer ") else "")
        if key != self.api_key:
            return _err(401, "Invalid or missing API key.")
        return await handler(request)

    async def h_sports(self, _):
        return web.json_response([{"key": "soccer_generic", "group": "Soccer", "title": "Liga Genérica",
                                   "active": True, "has_outcomes": True}])

    async def h_odds(self, req):
        out = []
        for e in self.events.values():
            if e["settled"]:
                continue
            out.append({"id": e["id"], "sport_key": "soccer_generic",
                        "commence_time": self.world.clock.display_iso(e["commence_real"]),
                        "home_team": e["home"], "away_team": e["away"],
                        "bookmakers": [{"key": "bookmaker", "title": "Bookmaker",
                                        "markets": [{"key": "h2h", "outcomes": [
                                            {"name": e["home"], "price": e["odd_home"]},
                                            {"name": e["away"], "price": e["odd_away"]}]}]}]})
        return web.json_response(out)

    async def h_bet(self, req):
        try:
            b = await req.json()
        except Exception:
            return _err(422, "Invalid JSON")
        e = self.events.get(b.get("event_id"))
        outcome = b.get("outcome")            # "home" o "away"
        if not e or e["settled"]:
            return _err(404, "Event not available.")
        if outcome not in ("home", "away"):
            return _err(422, "outcome must be home or away.")
        try:
            stake = float(b.get("stake"))
        except (TypeError, ValueError):
            return _err(422, "stake required.")
        if stake <= 0:
            return _err(422, "stake must be positive.")
        if not self.world.pay(to_cents(stake / self.fx), f"Apuesta {e['id']} {outcome}", COUNTERPARTY, ref=e["id"]):
            return _err(402, "Insufficient balance.")
        bid = "bet_" + secrets.token_hex(8)
        odd = e["odd_home"] if outcome == "home" else e["odd_away"]
        bet = {"id": bid, "event_id": e["id"], "outcome": outcome, "stake": stake, "odd": odd,
               "status": "open", "placed_at": self.world.clock.display_iso(), "payout": 0.0}
        self.bets[bid] = bet
        self.world.clock.schedule(e["commence_real"] + timedelta(days=self.cfg["settle_delay_days"]),
                                  lambda: self._settle(e["id"]), "betting:settle")
        return web.json_response(bet, status=200)

    def _settle(self, event_id: str) -> None:
        e = self.events.get(event_id)
        if not e or e["settled"]:
            return
        rng = self._rng(f"result:{event_id}")
        home_wins = rng.random() < e["p_home"]        # con la probabilidad VERDADERA
        e["result"] = "home" if home_wins else "away"
        e["settled"] = True
        for bet in self.bets.values():
            if bet["event_id"] != event_id or bet["status"] != "open":
                continue
            if bet["outcome"] == e["result"]:
                payout = bet["stake"] * bet["odd"]
                bet["payout"] = payout
                bet["status"] = "won"
                self.world.receive(to_cents(payout / self.fx), f"Premio apuesta {event_id}", COUNTERPARTY, ref=event_id)
            else:
                bet["status"] = "lost"

    async def h_get_bet(self, req):
        b = self.bets.get(req.match_info["id"])
        return web.json_response(b) if b else _err(404, "Bet not found.")
