"""Gemelo de la Google Ads API (googleads.googleapis.com).

customers, campaigns (mutate), y reportes vía searchStream (GAQL). Auth: Bearer +
developer-token. Mismo motor de campañas que Meta pero con los benchmarks de Google.
"""
from __future__ import annotations

import secrets
from typing import Optional

from aiohttp import web

from ..ads.platform import AdManager
from ..world import World

HOST = "googleads.googleapis.com"
API = "/v17"


def _err(status: int, message: str, code: str = "INVALID_ARGUMENT") -> web.Response:
    return web.json_response({"error": {"code": status, "message": message, "status": code}}, status=status)


class GoogleAdsTwin:
    host = HOST

    def __init__(self, world: World, access_token: Optional[str] = None,
                 developer_token: Optional[str] = None, customer_id: Optional[str] = None):
        self.world = world
        self.mgr = AdManager(world, "google")
        self.token = access_token or "ya29." + secrets.token_hex(24)
        self.dev_token = developer_token or secrets.token_hex(11)
        self.customer_id = customer_id or f"{secrets.randbelow(900)+100}{secrets.randbelow(9000000)+1000000:07d}"
        world.register("google_ads", self)

    def _auth_ok(self, req) -> bool:
        h = req.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        return tok == self.token and req.headers.get("developer-token") == self.dev_token

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get(API + "/customers/{cid}", self.h_customer)
        r.add_post(API + "/customers/{cid}/campaigns:mutate", self.h_mutate)
        r.add_post(API + "/customers/{cid}/googleAds:searchStream", self.h_search_stream)
        return app

    async def h_customer(self, req):
        if not self._auth_ok(req):
            return _err(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
        cid = req.match_info["cid"]
        return web.json_response({"resourceName": f"customers/{cid}",
                                  "id": cid, "descriptiveName": "Cuenta", "currencyCode": "USD",
                                  "timeZone": "Europe/Madrid"})

    async def h_mutate(self, req):
        if not self._auth_ok(req):
            return _err(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
        cid = req.match_info["cid"]
        try:
            body = await req.json()
        except Exception:
            return _err(400, "Invalid JSON")
        if self.world.live.block("google_ads", "mutate_campaign",
                                 {"ops": len(body.get("operations", []))}):
            # Modo en vivo: no se crean ni modifican campañas, no se gasta (nada sale).
            return web.json_response({"results": []})
        results = []
        for op in body.get("operations", []):
            create = op.get("create")
            if create:
                name = create.get("name")
                # presupuesto: campaignBudget en micros (1e6) o daily_budget_usd (extensión)
                daily = None
                if "daily_budget_usd" in create:
                    daily = float(create["daily_budget_usd"])
                elif "campaignBudgetMicros" in create:
                    daily = float(create["campaignBudgetMicros"]) / 1e6
                if not name or not daily or daily <= 0:
                    return _err(400, "name and budget are required")
                gid = str(secrets.randbelow(9_000_000_000) + 1_000_000_000)
                cat = create.get("category", "default")
                self.mgr.create_campaign(gid, name, cat, daily, float(create.get("appeal_quality", 5.0)))
                results.append({"resourceName": f"customers/{cid}/campaigns/{gid}"})
            elif op.get("update"):
                rn = op["update"].get("resourceName", "")
                gid = rn.rsplit("/", 1)[-1]
                if gid in self.mgr.campaigns and op["update"].get("status"):
                    self.mgr.campaigns[gid].status = op["update"]["status"]
                results.append({"resourceName": rn})
        return web.json_response({"results": results})

    async def h_search_stream(self, req):
        if not self._auth_ok(req):
            return _err(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
        rows = []
        for c in self.mgr.campaigns.values():
            ins = self.mgr.insights(c.id)
            rows.append({
                "campaign": {"resourceName": f"customers/{req.match_info['cid']}/campaigns/{c.id}",
                             "id": c.id, "name": c.name, "status": c.status},
                "metrics": {"impressions": ins["impressions"], "clicks": ins["clicks"],
                            "costMicros": str(int(round(c.spend_usd * 1e6))),
                            "ctr": ins["ctr"], "averageCpc": str(int(round(float(ins["cpc"]) * 1e6)))}})
        return web.json_response([{"results": rows}])
