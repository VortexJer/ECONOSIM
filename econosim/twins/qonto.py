"""Gemelo de la Qonto Business API v2 (https://docs.qonto.com): la cuenta bancaria de la IA.

Solo lectura: organización, cuenta con saldo, y transacciones (= el ledger).
Auth como la real: `Authorization: <organization-slug>:<secret-key>`.
"""
from __future__ import annotations

import secrets
from typing import Optional

from aiohttp import web

from ..ledger import Entry
from ..world import World, BANK

HOST = "thirdparty.qonto.com"


def _err(status: int, message: str) -> web.Response:
    return web.json_response({"errors": [{"code": str(status), "detail": message}]}, status=status)


class QontoTwin:
    host = HOST

    def __init__(self, world: World, org_slug: str = "vps-labs-1234", secret_key: Optional[str] = None):
        self.world = world
        self.clock = world.clock
        self.org_slug = org_slug
        self.secret_key = secret_key or secrets.token_hex(16)
        self.iban = "ES91 2100 0418 4502 0005 1332".replace(" ", "")
        self.bic = "QNTOESB2XXX"
        self.account_slug = f"{org_slug}-bank-account-1"
        world.register("qonto", self)

    @property
    def auth_header(self) -> str:
        return f"{self.org_slug}:{self.secret_key}"

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        app.router.add_get("/v2/organization", self.h_org)
        app.router.add_get("/v2/organizations/{slug}", self.h_org)
        app.router.add_get("/v2/transactions", self.h_transactions)
        app.router.add_get("/v2/transactions/{id}", self.h_transaction)
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        if request.headers.get("Authorization", "") != self.auth_header:
            return _err(401, "Unauthorized")
        return await handler(request)

    def _account(self) -> dict:
        bal = self.world.balance()
        return {"slug": self.account_slug, "iban": self.iban, "bic": self.bic, "currency": "EUR",
                "balance": bal / 100, "balance_cents": bal, "authorized_balance": bal / 100,
                "authorized_balance_cents": bal, "name": "Cuenta principal", "status": "active",
                "main": True, "updated_at": self.clock.display_iso()}

    async def h_org(self, _):
        return web.json_response({"organization": {"slug": self.org_slug, "legal_name": "VPS Labs S.L.",
                                                   "locale": "es", "legal_country": "ES",
                                                   "bank_accounts": [self._account()]}})

    def _tx(self, e: Entry) -> dict:
        debit = e.amount_cents < 0
        cp = e.counterparty or ("Ingreso" if not debit else "Cargo")
        return {"transaction_id": f"{self.account_slug}-{e.id}-transaction", "amount": abs(e.amount_cents) / 100,
                "amount_cents": abs(e.amount_cents), "local_amount": abs(e.amount_cents) / 100,
                "local_amount_cents": abs(e.amount_cents), "side": "debit" if debit else "credit",
                "operation_type": "card" if debit else "income", "currency": "EUR", "local_currency": "EUR",
                "label": cp, "settled_at": e.display_ts, "emitted_at": e.display_ts, "updated_at": e.display_ts,
                "status": "completed", "note": e.concept, "reference": e.ref, "vat_amount": None,
                "vat_rate": None, "initiator_id": None, "label_ids": [], "attachment_ids": [],
                "card_last_digits": "4421" if debit else None, "category": "other",
                "subject_type": "Card" if debit else "Income"}

    async def h_transactions(self, req):
        entries = self.world.ledger.entries(BANK)
        side = req.query.get("side")
        txs = [self._tx(e) for e in reversed(entries)]
        if side in ("debit", "credit"):
            txs = [t for t in txs if t["side"] == side]
        per_page = max(1, min(int(req.query.get("per_page", 100)), 100))
        page = max(1, int(req.query.get("current_page", 1)))
        total = len(txs)
        chunk = txs[(page - 1) * per_page: page * per_page]
        return web.json_response({"transactions": chunk, "meta": {
            "current_page": page, "next_page": page + 1 if page * per_page < total else None,
            "prev_page": page - 1 if page > 1 else None, "total_pages": max(1, -(-total // per_page)),
            "total_count": total, "per_page": per_page}})

    async def h_transaction(self, req):
        tid = req.match_info["id"]
        for e in self.world.ledger.entries(BANK):
            if f"{self.account_slug}-{e.id}-transaction" == tid:
                return web.json_response({"transaction": self._tx(e)})
        return _err(404, "Not found")
