"""Gemelo de la Stripe API (https://docs.stripe.com/api). Pasarela de pagos de la IA.

La IA crea productos/precios y cobra; Stripe retiene su comisión (2,9% + $0,30),
acumula el neto en el saldo, y lo liquida al banco (Qonto/ledger) con el plazo
real (payout rolling T+2; cuentas nuevas con retención inicial). Disputas y
reembolsos devuelven dinero y cobran la comisión de disputa.

Auth: `Authorization: Bearer sk_...` (clave secreta). Importes en centavos, USD.
"""
from __future__ import annotations

import secrets
import time
from datetime import timedelta
from typing import Optional

from aiohttp import web

from ..ledger import to_cents
from ..world import World

HOST = "api.stripe.com"
COUNTERPARTY = "Stripe Payments"


def _err(status: int, message: str, code: str = "resource_missing", typ: str = "invalid_request_error") -> web.Response:
    return web.json_response({"error": {"message": message, "type": typ, "code": code}}, status=status)


def _form(data: bytes) -> dict:
    from urllib.parse import parse_qsl
    return dict(parse_qsl(data.decode("utf-8", "replace")))


class StripeTwin:
    host = HOST

    def __init__(self, world: World, secret_key: Optional[str] = None):
        self.world = world
        self.clock = world.clock
        self.cfg = world.load_pricing("stripe")
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.secret_key = secret_key or "sk_live_" + secrets.token_hex(24)
        self.pub_key = "pk_live_" + secrets.token_hex(24)
        self.products: dict[str, dict] = {}
        self.prices: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self.disputes: dict[str, dict] = {}
        self.balance_txns: list[dict] = []
        self.payouts: list[dict] = []
        self.available_cents = 0        # USD cents disponibles para payout
        self.pending_cents = 0          # USD cents aún en tránsito (no liquidados)
        self.charge_count = 0
        self._seq = 0
        self.on_price = None        # callback(product, price) para el puente de mercado
        world.register("stripe", self)
        self._schedule_payout_cycle()

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{secrets.token_hex(12)}"

    # ---- API interna para el resolutor ----------------------------------
    def record_sale(self, amount_usd: float, description: str = "", ref: str = "") -> dict:
        """Cobra una venta: crea charge, retiene comisión, suma el neto a pendiente."""
        gross = to_cents(amount_usd)
        fee = to_cents(amount_usd * self.cfg["fee_percent"]) + to_cents(self.cfg["fee_fixed_usd"])
        fee = min(fee, gross)
        net = gross - fee
        self.charge_count += 1
        cid = self._id("ch")
        charge = {"id": cid, "object": "charge", "amount": gross, "amount_captured": gross,
                  "amount_refunded": 0, "currency": "usd", "paid": True, "captured": True,
                  "status": "succeeded", "refunded": False, "disputed": False,
                  "description": description, "created": int(self.clock.display_now.timestamp()),
                  "created_iso": self.clock.display_iso(), "metadata": {"ref": ref},
                  "fee": fee, "net": net}
        self.charges[cid] = charge
        self._add_balance_txn("charge", gross, fee, net, cid)
        # las cuentas nuevas retienen los primeros cobros más tiempo
        hold = self.cfg["payout"]["new_account_hold_days"] if self.charge_count <= self.cfg["payout"]["new_account_charges_threshold"] else self.cfg["payout"]["rolling_days"]
        self.pending_cents += net
        self.clock.schedule_in(timedelta(days=hold), lambda: self._make_available(net), "stripe:settle")
        return charge

    def refund(self, charge_id: str, dispute: bool = False) -> Optional[dict]:
        ch = self.charges.get(charge_id)
        if not ch or ch["refunded"]:
            return None
        gross = ch["amount"]
        ch["refunded"] = True
        ch["amount_refunded"] = gross
        # devolvemos el bruto al cliente; la comisión original NO se recupera
        claw = gross
        if dispute:
            ch["disputed"] = True
            claw += to_cents(self.cfg["dispute_fee_usd"])
            did = self._id("dp")
            self.disputes[did] = {"id": did, "object": "dispute", "charge": charge_id,
                                  "amount": gross, "reason": "fraudulent", "status": "lost",
                                  "created": int(self.clock.display_now.timestamp())}
        rid = self._id("re")
        self.refunds[rid] = {"id": rid, "object": "refund", "charge": charge_id, "amount": gross,
                             "status": "succeeded", "created": int(self.clock.display_now.timestamp())}
        # se descuenta de disponible (o pendiente); puede dejar el saldo Stripe negativo
        self.available_cents -= claw
        self._add_balance_txn("dispute" if dispute else "refund", -claw, 0, -claw, charge_id)
        return self.refunds[rid]

    def _make_available(self, net_cents: int) -> None:
        self.pending_cents -= net_cents
        self.available_cents += net_cents

    def _add_balance_txn(self, typ: str, amount: int, fee: int, net: int, src: str) -> None:
        self.balance_txns.append({"id": self._id("txn"), "object": "balance_transaction", "type": typ,
                                  "amount": amount, "fee": fee, "net": net, "currency": "usd",
                                  "source": src, "created": int(self.clock.display_now.timestamp()),
                                  "status": "available"})

    # ---- payout automático al banco -------------------------------------
    def _schedule_payout_cycle(self) -> None:
        self.clock.schedule_in(timedelta(days=1), self._payout_cycle, "stripe:payout")

    def _payout_cycle(self) -> None:
        if self.available_cents > 0:
            amount = self.available_cents
            self.available_cents = 0
            eur = to_cents((amount / 100.0) / self.fx)
            self.world.receive(eur, "Payout de Stripe", COUNTERPARTY, ref="payout")
            self.payouts.append({"id": self._id("po"), "object": "payout", "amount": amount,
                                 "currency": "usd", "status": "paid", "method": "standard",
                                 "arrival_date": int(self.clock.display_now.timestamp()),
                                 "created_iso": self.clock.display_iso()})
        elif self.available_cents < 0:
            # saldo negativo por reembolsos: Stripe lo carga a la cuenta bancaria
            debt = -self.available_cents
            self.available_cents = 0
            eur = to_cents((debt / 100.0) / self.fx)
            self.world.pay(eur, "Ajuste negativo de Stripe (reembolsos)", COUNTERPARTY, ref="clawback")
        self._schedule_payout_cycle()

    def balance_cents(self) -> int:
        return self.available_cents + self.pending_cents

    # ================================================================= HTTP
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_post("/v1/products", self.h_create_product)
        r.add_get("/v1/products", self.h_list_products)
        r.add_get("/v1/products/{id}", self.h_get_product)
        r.add_post("/v1/prices", self.h_create_price)
        r.add_get("/v1/prices", self.h_list_prices)
        r.add_post("/v1/checkout/sessions", self.h_create_session)
        r.add_get("/v1/checkout/sessions/{id}", self.h_get_session)
        r.add_get("/v1/charges", self.h_list_charges)
        r.add_get("/v1/charges/{id}", self.h_get_charge)
        r.add_get("/v1/balance", self.h_balance)
        r.add_get("/v1/balance_transactions", self.h_balance_txns)
        r.add_get("/v1/payouts", self.h_list_payouts)
        r.add_post("/v1/refunds", self.h_create_refund)
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.secret_key}":
            return _err(401, "Invalid API Key provided", "api_key_invalid", "authentication_error")
        return await handler(request)

    async def _body(self, req) -> dict:
        raw = await req.read()
        ctype = req.headers.get("Content-Type", "")
        if "application/json" in ctype:
            import json
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}
        return _form(raw)

    async def h_create_product(self, req):
        b = await self._body(req)
        if not b.get("name"):
            return _err(400, "Missing required param: name.", "parameter_missing")
        if self.world.live.block("stripe", "create_product", {"name": b.get("name")}):
            pid = self._id("prod")
            return web.json_response({"id": pid, "object": "product", "name": b["name"], "active": True,
                                      "description": b.get("description", ""),
                                      "created": int(self.clock.display_now.timestamp()),
                                      "livemode": True, "metadata": {}})
        meta = {}
        for k, v in b.items():
            if k.startswith("metadata[") and k.endswith("]"):
                meta[k[9:-1]] = v
        pid = self._id("prod")
        p = {"id": pid, "object": "product", "name": b["name"], "active": True,
             "description": b.get("description", ""), "created": int(self.clock.display_now.timestamp()),
             "livemode": True, "metadata": meta}
        self.products[pid] = p
        return web.json_response(p)

    async def h_list_products(self, _):
        return web.json_response({"object": "list", "url": "/v1/products", "has_more": False,
                                  "data": list(self.products.values())})

    async def h_get_product(self, req):
        p = self.products.get(req.match_info["id"])
        return web.json_response(p) if p else _err(404, "No such product", "resource_missing")

    async def h_create_price(self, req):
        b = await self._body(req)
        prod = b.get("product")
        amt = b.get("unit_amount")
        if not prod or prod not in self.products:
            return _err(400, "No such product", "resource_missing")
        try:
            amount = int(amt)
        except (TypeError, ValueError):
            return _err(400, "Invalid unit_amount", "parameter_invalid_integer")
        if self.world.live.block("stripe", "create_price", {"product": prod, "unit_amount": amount}):
            # Modo en vivo: no se registra el precio ni se dispara el bucle de ventas (nada sale).
            return web.json_response({"id": self._id("price"), "object": "price", "product": prod,
                                      "unit_amount": amount, "currency": b.get("currency", "usd"),
                                      "active": True, "type": "one_time",
                                      "created": int(self.clock.display_now.timestamp()), "livemode": True})
        pid = self._id("price")
        pr = {"id": pid, "object": "price", "product": prod, "unit_amount": amount,
              "currency": b.get("currency", "usd"), "active": True, "type": "one_time",
              "created": int(self.clock.display_now.timestamp()), "livemode": True}
        self.prices[pid] = pr
        if self.on_price is not None:
            try:
                self.on_price(self.products.get(prod), pr)
            except Exception:
                pass
        return web.json_response(pr)

    async def h_list_prices(self, _):
        return web.json_response({"object": "list", "url": "/v1/prices", "has_more": False,
                                  "data": list(self.prices.values())})

    async def h_create_session(self, req):
        b = await self._body(req)
        sid = self._id("cs")
        if self.world.live.block("stripe", "create_session", {"mode": b.get("mode", "payment")}):
            return web.json_response({"id": sid, "object": "checkout.session", "mode": b.get("mode", "payment"),
                                      "status": "open", "payment_status": "unpaid",
                                      "url": f"https://checkout.stripe.com/c/pay/{sid}",
                                      "success_url": b.get("success_url", ""), "cancel_url": b.get("cancel_url", ""),
                                      "currency": "usd", "created": int(self.clock.display_now.timestamp()),
                                      "livemode": True})
        sess = {"id": sid, "object": "checkout.session", "mode": b.get("mode", "payment"),
                "status": "open", "payment_status": "unpaid",
                "url": f"https://checkout.stripe.com/c/pay/{sid}",
                "success_url": b.get("success_url", ""), "cancel_url": b.get("cancel_url", ""),
                "currency": "usd", "created": int(self.clock.display_now.timestamp()), "livemode": True}
        self.sessions[sid] = sess
        return web.json_response(sess)

    async def h_get_session(self, req):
        s = self.sessions.get(req.match_info["id"])
        return web.json_response(s) if s else _err(404, "No such session", "resource_missing")

    async def h_list_charges(self, _):
        return web.json_response({"object": "list", "url": "/v1/charges", "has_more": False,
                                  "data": sorted(self.charges.values(), key=lambda c: -c["created"])[:100]})

    async def h_get_charge(self, req):
        c = self.charges.get(req.match_info["id"])
        return web.json_response(c) if c else _err(404, "No such charge", "resource_missing")

    async def h_balance(self, _):
        return web.json_response({"object": "balance", "livemode": True,
                                  "available": [{"amount": self.available_cents, "currency": "usd", "source_types": {"card": self.available_cents}}],
                                  "pending": [{"amount": self.pending_cents, "currency": "usd", "source_types": {"card": self.pending_cents}}]})

    async def h_balance_txns(self, _):
        return web.json_response({"object": "list", "url": "/v1/balance_transactions", "has_more": False,
                                  "data": self.balance_txns[-100:]})

    async def h_list_payouts(self, _):
        return web.json_response({"object": "list", "url": "/v1/payouts", "has_more": False,
                                  "data": self.payouts[-100:]})

    async def h_create_refund(self, req):
        b = await self._body(req)
        ch = b.get("charge")
        if not ch or ch not in self.charges:
            return _err(404, "No such charge", "resource_missing")
        if self.world.live.block("stripe", "create_refund", {"charge": ch}):
            # Modo en vivo: no se mueve dinero (nada sale).
            return web.json_response({"id": self._id("re"), "object": "refund", "charge": ch,
                                      "status": "succeeded", "amount": self.charges[ch].get("amount", 0),
                                      "currency": "usd"})
        r = self.refund(ch, dispute=False)
        if r is None:
            return _err(400, "Charge has already been refunded", "charge_already_refunded")
        return web.json_response(r)
