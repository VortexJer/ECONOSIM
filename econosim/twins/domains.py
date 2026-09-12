"""Gemelo de un registrador de dominios (estilo Porkbun JSON API v3).

Disponibilidad, alta (cobra el precio real anual), listado, renovación. Un dominio
dado de alta ocupa el nombre y caduca al año; si no se renueva dentro del periodo
de gracia, queda libre y un ocupa-dominios sintético puede pillarlo.

Auth: apikey + secretapikey en el cuerpo JSON (como Porkbun).
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from aiohttp import web

from ..ledger import to_cents
from ..world import World

HOST = "api.porkbun.com"
API = "/api/json/v3"
COUNTERPARTY = "Porkbun LLC"


class DomainsTwin:
    host = HOST

    def __init__(self, world: World, apikey: Optional[str] = None, secret: Optional[str] = None):
        self.world = world
        self.cfg = world.load_pricing("services")["domains"]
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.apikey = apikey or "pk1_" + secrets.token_hex(16)
        self.secret = secret or "sk1_" + secrets.token_hex(16)
        self.owned: dict[str, dict] = {}         # domain -> {expiry_real, status}
        self.taken_by_others: set[str] = set()   # ocupados por terceros sintéticos
        world.register("domains", self)

    def price_of(self, domain: str) -> float:
        tld = domain.rsplit(".", 1)[-1].lower()
        return self.cfg["tld_price_usd"].get(tld, self.cfg["default_price_usd"])

    def _available(self, domain: str) -> bool:
        d = domain.lower()
        return d not in self.owned and d not in self.taken_by_others

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_post(API + "/domain/checkDomain/{domain}", self.h_check)
        r.add_post(API + "/domain/register/{domain}", self.h_register)
        r.add_post(API + "/domain/renew/{domain}", self.h_renew)
        r.add_post(API + "/domain/listAll", self.h_list)
        r.add_post(API + "/ping", self.h_ping)
        return app

    async def _auth(self, req) -> Optional[dict]:
        try:
            b = await req.json()
        except Exception:
            b = {}
        if b.get("apikey") != self.apikey or b.get("secretapikey") != self.secret:
            return None
        return b

    def _fail(self, message: str, status: int = 400) -> web.Response:
        return web.json_response({"status": "ERROR", "message": message}, status=status)

    async def h_ping(self, req):
        if await self._auth(req) is None:
            return self._fail("Invalid API key.", 403)
        return web.json_response({"status": "SUCCESS", "yourIp": "203.0.113.7"})

    async def h_check(self, req):
        if await self._auth(req) is None:
            return self._fail("Invalid API key.", 403)
        domain = req.match_info["domain"].lower()
        avail = self._available(domain)
        return web.json_response({"status": "SUCCESS", "response": {
            "avail": "yes" if avail else "no",
            "type": "registration", "price": f"{self.price_of(domain):.2f}",
            "premium": "no"}})

    async def h_register(self, req):
        if await self._auth(req) is None:
            return self._fail("Invalid API key.", 403)
        domain = req.match_info["domain"].lower()
        if not self._available(domain):
            return self._fail("Domain is not available for registration.")
        price = self.price_of(domain)
        if self.world.live.block("domains", "register", {"domain": domain, "price_usd": price}):
            # Modo en vivo: ni se cobra ni se da de alta el dominio (nada sale).
            expiry = self.world.clock.real_now() + timedelta(days=365)
            return web.json_response({"status": "SUCCESS", "domain": domain,
                                      "expiryDate": self.world.clock.display_iso(expiry)})
        if not self.world.pay(to_cents(price / self.fx), f"Registro dominio {domain}", COUNTERPARTY, ref=domain):
            return self._fail("Insufficient balance.", 402)
        expiry = self.world.clock.real_now() + timedelta(days=365)
        self.owned[domain] = {"expiry_real": expiry, "status": "active"}
        self.world.clock.schedule(expiry, lambda d=domain: self._expire(d), "domains:expiry")
        return web.json_response({"status": "SUCCESS", "domain": domain,
                                  "expiryDate": self.world.clock.display_iso(expiry)})

    async def h_renew(self, req):
        if await self._auth(req) is None:
            return self._fail("Invalid API key.", 403)
        domain = req.match_info["domain"].lower()
        d = self.owned.get(domain)
        if not d or d["status"] != "active":
            return self._fail("Domain not in your account.")
        price = self.price_of(domain)
        if self.world.live.block("domains", "renew", {"domain": domain, "price_usd": price}):
            return web.json_response({"status": "SUCCESS", "domain": domain,
                                      "expiryDate": self.world.clock.display_iso(d["expiry_real"])})
        if not self.world.pay(to_cents(price / self.fx), f"Renovación dominio {domain}", COUNTERPARTY, ref=domain):
            return self._fail("Insufficient balance.", 402)
        d["expiry_real"] = max(d["expiry_real"], self.world.clock.real_now()) + timedelta(days=365)
        self.world.clock.schedule(d["expiry_real"], lambda dd=domain: self._expire(dd), "domains:expiry")
        return web.json_response({"status": "SUCCESS", "domain": domain,
                                  "expiryDate": self.world.clock.display_iso(d["expiry_real"])})

    async def h_list(self, req):
        if await self._auth(req) is None:
            return self._fail("Invalid API key.", 403)
        domains = [{"domain": d, "status": info["status"],
                    "expiryDate": self.world.clock.display_iso(info["expiry_real"])}
                   for d, info in self.owned.items() if info["status"] == "active"]
        return web.json_response({"status": "SUCCESS", "domains": domains})

    def _expire(self, domain: str) -> None:
        info = self.owned.get(domain)
        if not info or info["status"] != "active":
            return
        # solo caduca si su expiry real ya pasó (una renovación mueve la fecha)
        if info["expiry_real"] > self.world.clock.real_now():
            return
        grace = self.cfg["renewal_grace_days"]
        self.world.clock.schedule_in(timedelta(days=grace), lambda d=domain: self._release(d), "domains:release")
        info["status"] = "expired"

    def _release(self, domain: str) -> None:
        info = self.owned.get(domain)
        if info and info["status"] == "expired":
            del self.owned[domain]
            self.taken_by_others.add(domain)      # lo pilla un ocupa-dominios sintético
