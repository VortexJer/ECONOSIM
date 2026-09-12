"""Gemelo de la Meta Marketing API (graph.facebook.com).

adaccount, campaigns, adsets, ads, insights. La IA crea una campaña con presupuesto
diario; el motor gasta a diario y el informe (impresiones/clics/gasto) sale de los
benchmarks reales de Meta. Auth: access_token (query o Bearer).

Para vincular la campaña a un producto se acepta el campo `category` (una de las
categorías del resolutor); es la extensión mínima del simulador sobre el esquema real.
"""
from __future__ import annotations

import secrets
from typing import Optional

from aiohttp import web

from ..ads.platform import AdManager
from ..world import World

HOST = "graph.facebook.com"
API = "/v22.0"


def _err(status: int, message: str, code: int = 100) -> web.Response:
    return web.json_response({"error": {"message": message, "type": "OAuthException", "code": code}}, status=status)


class MetaAdsTwin:
    host = HOST

    def __init__(self, world: World, access_token: Optional[str] = None, account_id: Optional[str] = None):
        self.world = world
        self.mgr = AdManager(world, "meta")
        self.token = access_token or "EAA" + secrets.token_hex(20)
        self.account_id = account_id or str(secrets.randbelow(9_000_000) + 1_000_000)
        world.register("meta_ads", self)

    def _auth_ok(self, req) -> bool:
        tok = req.query.get("access_token") or ""
        if not tok:
            h = req.headers.get("Authorization", "")
            tok = h[7:] if h.startswith("Bearer ") else ""
        return tok == self.token

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get(f"{API}/act_{{acc}}", self.h_account)
        r.add_get(f"{API}/act_{{acc}}/campaigns", self.h_list_campaigns)
        r.add_post(f"{API}/act_{{acc}}/campaigns", self.h_create_campaign)
        r.add_get(API + "/{cid}/insights", self.h_insights)
        r.add_post(API + "/{cid}", self.h_update)
        return app

    async def _body(self, req) -> dict:
        raw = await req.read()
        from urllib.parse import parse_qsl
        d = dict(parse_qsl(raw.decode("utf-8", "replace")))
        d.update({k: v for k, v in req.query.items() if k != "access_token"})
        return d

    async def h_account(self, req):
        if not self._auth_ok(req):
            return _err(401, "Invalid OAuth access token")
        return web.json_response({"id": f"act_{req.match_info['acc']}", "account_id": req.match_info["acc"],
                                  "name": "Ad Account", "currency": "USD", "account_status": 1,
                                  "amount_spent": str(int(sum(c.spend_usd for c in self.mgr.campaigns.values()) * 100))})

    async def h_create_campaign(self, req):
        if not self._auth_ok(req):
            return _err(401, "Invalid OAuth access token")
        b = await self._body(req)
        if not b.get("name"):
            return _err(400, "(#100) Missing required parameter: name")
        # presupuesto diario en centavos (como Meta); o daily_budget_usd (extensión)
        daily = None
        if b.get("daily_budget"):
            try:
                daily = int(b["daily_budget"]) / 100.0
            except ValueError:
                return _err(400, "Invalid daily_budget")
        elif b.get("daily_budget_usd"):
            daily = float(b["daily_budget_usd"])
        if not daily or daily <= 0:
            return _err(400, "(#100) daily_budget is required")
        cid = str(secrets.randbelow(9_000_000_000) + 10_000_000_000)
        if self.world.live.block("meta_ads", "create_campaign", {"name": b.get("name"), "daily_budget_usd": daily}):
            # Modo en vivo: no se crea la campaña ni se gasta presupuesto (nada sale).
            return web.json_response({"id": cid})
        cat = b.get("category", "default")
        c = self.mgr.create_campaign(cid, b["name"], cat, daily, float(b.get("appeal_quality", 5.0)))
        c._objective = b.get("objective", "OUTCOME_TRAFFIC")
        return web.json_response({"id": cid})

    async def h_list_campaigns(self, req):
        if not self._auth_ok(req):
            return _err(401, "Invalid OAuth access token")
        data = [{"id": c.id, "name": c.name, "status": c.status,
                 "objective": getattr(c, "_objective", "OUTCOME_TRAFFIC"),
                 "daily_budget": str(int(c.daily_budget_usd * 100))} for c in self.mgr.campaigns.values()]
        return web.json_response({"data": data, "paging": {"cursors": {"before": "", "after": ""}}})

    async def h_insights(self, req):
        if not self._auth_ok(req):
            return _err(401, "Invalid OAuth access token")
        cid = req.match_info["cid"]
        c = self.mgr.campaigns.get(cid)
        if not c:
            return _err(400, "(#803) Unknown campaign")
        ins = self.mgr.insights(cid)
        return web.json_response({"data": [{
            "campaign_id": cid, "campaign_name": c.name,
            "impressions": ins["impressions"], "clicks": ins["clicks"], "spend": ins["spend"],
            "cpc": ins["cpc"], "ctr": ins["ctr"], "cpm": ins["cpm"], "reach": ins["reach"],
            "date_start": self.world.clock.display_now.strftime("%Y-%m-%d"),
            "date_stop": self.world.clock.display_now.strftime("%Y-%m-%d")}],
            "paging": {"cursors": {"before": "", "after": ""}}})

    async def h_update(self, req):
        if not self._auth_ok(req):
            return _err(401, "Invalid OAuth access token")
        cid = req.match_info["cid"]
        c = self.mgr.campaigns.get(cid)
        if not c:
            return _err(400, "(#803) Unknown campaign")
        b = await self._body(req)
        if self.world.live.block("meta_ads", "update_campaign", {"id": cid, "status": b.get("status")}):
            return web.json_response({"success": True})
        if b.get("status"):
            c.status = b["status"]
        return web.json_response({"success": True})
