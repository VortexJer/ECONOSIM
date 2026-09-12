"""Gemelo de un servicio de correo (estilo Resend API).

Envío (tramo gratis mensual + coste por email), bandeja de entrada que recibe
mensajes de clientes/adversarios sintéticos, y rebotes a direcciones inválidas.
Auth: Authorization: Bearer re_...
"""
from __future__ import annotations

import secrets
from typing import Optional

from aiohttp import web

from ..ledger import to_cents
from ..world import World

HOST = "api.resend.com"
COUNTERPARTY = "Resend, Inc."


def _err(status: int, message: str, name: str = "validation_error") -> web.Response:
    return web.json_response({"statusCode": status, "message": message, "name": name}, status=status)


class EmailTwin:
    host = HOST

    def __init__(self, world: World, api_key: Optional[str] = None):
        self.world = world
        self.cfg = world.load_pricing("services")["email"]
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.api_key = api_key or "re_" + secrets.token_hex(16)
        self.sent: list[dict] = []
        self.inbox: list[dict] = []
        self.sent_this_month = 0
        self.owed_usd = 0.0
        self._reset_month()
        world.register("email", self)

    def _reset_month(self) -> None:
        # a fin de mes se factura lo acumulado (los envíos cuestan fracciones de céntimo)
        self._flush()
        self.sent_this_month = 0
        from datetime import timedelta
        self.world.clock.schedule_in(timedelta(days=30), self._reset_month, "email:month")

    def _flush(self) -> None:
        if self.owed_usd > 0:
            cents = to_cents(self.owed_usd / self.fx)
            if cents > 0:
                self.world.pay(cents, "Factura de correo del mes", COUNTERPARTY, ref="email")
                self.owed_usd = 0.0

    # ---- API interna: entregar un mensaje a la bandeja (clientes/adversarios) ----
    def deliver(self, from_addr: str, subject: str, body: str, kind: str = "customer") -> dict:
        msg = {"id": "in_" + secrets.token_hex(8), "from": from_addr, "subject": subject,
               "text": body, "kind": kind, "received_at": self.world.clock.display_iso(), "read": False}
        self.inbox.append(msg)
        return msg

    def _bounces(self, to_addr: str) -> bool:
        dom = to_addr.split("@")[-1].split(".")[0].lower() if "@" in to_addr else ""
        return ("@" not in to_addr) or dom in self.cfg["bounce_domains"]

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_post("/emails", self.h_send)
        r.add_get("/emails/{id}", self.h_get)
        r.add_get("/inbox", self.h_inbox)         # extensión del simulador para leer la bandeja
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        if request.headers.get("Authorization", "") != f"Bearer {self.api_key}":
            return _err(401, "Missing API key in the authorization header.", "missing_api_key")
        return await handler(request)

    async def h_send(self, req):
        try:
            b = await req.json()
        except Exception:
            return _err(422, "Invalid JSON")
        to = b.get("to")
        tos = to if isinstance(to, list) else [to] if to else []
        if not b.get("from") or not tos or not b.get("subject"):
            return _err(422, "Missing required field: from, to or subject.")
        if self.world.live.block("email", "send", {"to": tos, "subject": b.get("subject")}):
            # Modo en vivo: nada se entrega ni se contabiliza (ninguna acción sale).
            return web.json_response({"id": "em_" + secrets.token_hex(8)}, status=200)
        # rebote si alguna dirección es inválida
        bounced = [t for t in tos if self._bounces(str(t))]
        # coste: gratis hasta el tramo, luego por email
        n = len(tos)
        chargeable = max(0, (self.sent_this_month + n) - self.cfg["free_per_month"])
        chargeable = min(chargeable, n)
        if chargeable > 0:
            self.owed_usd += chargeable * self.cfg["price_per_email_usd"]
        self.sent_this_month += n
        mid = "em_" + secrets.token_hex(8)
        rec = {"id": mid, "from": b["from"], "to": tos, "subject": b["subject"],
               "created_at": self.world.clock.display_iso(),
               "last_event": "bounced" if bounced else "delivered", "bounced": bounced}
        self.sent.append(rec)
        return web.json_response({"id": mid}, status=200)

    async def h_get(self, req):
        for r in self.sent:
            if r["id"] == req.match_info["id"]:
                return web.json_response(r)
        return _err(404, "Email not found.", "not_found")

    async def h_inbox(self, req):
        return web.json_response({"data": self.inbox[-100:], "count": len(self.inbox)})
