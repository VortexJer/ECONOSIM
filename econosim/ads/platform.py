"""Motor de campañas compartido por los gemelos de anuncios.

Cada día (programado en el reloj) una campaña activa gasta su presupuesto diario:
corre el embudo (impresiones/clics/visitas con fatiga), cobra el gasto al banco y
acumula las métricas. Si el banco no puede pagar, la campaña se pausa (tarjeta
rechazada). Los clics acumulados por categoría alimentan el 'alcance' del resolutor.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..ledger import to_cents
from ..world import World
from .funnel import AdBenchmarks, ad_appeal, funnel, load_benchmarks, reach_from_clicks

COUNTERPARTY = {"meta": "Meta Platforms, Inc.", "google": "Google Ads"}


class Campaign:
    def __init__(self, cid: str, name: str, platform: str, category: str,
                 daily_budget_usd: float, appeal: float):
        self.id = cid
        self.name = name
        self.platform = platform
        self.category = category
        self.daily_budget_usd = daily_budget_usd
        self.appeal = appeal
        self.status = "ACTIVE"
        self.impressions = 0
        self.clicks = 0.0
        self.spend_usd = 0.0
        self.days = 0


class AdManager:
    """Un manager por plataforma (meta/google). Comparte reloj/ledger del mundo."""

    def __init__(self, world: World, platform: str, bench: Optional[AdBenchmarks] = None):
        self.world = world
        self.platform = platform
        self.bench = bench or load_benchmarks(world.pricing_dir)
        self.pcfg = self.bench.platform(platform)
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.campaigns: dict[str, Campaign] = {}
        self.clicks_by_category: dict[str, float] = {}
        self._cycle_started = False

    def create_campaign(self, cid: str, name: str, category: str,
                        daily_budget_usd: float, appeal_quality: float = 5.0) -> Campaign:
        c = Campaign(cid, name, self.platform, category, max(0.0, daily_budget_usd),
                     ad_appeal(appeal_quality))
        self.campaigns[cid] = c
        if not self._cycle_started:
            self._cycle_started = True
            self.world.clock.schedule_in(timedelta(days=1), self._daily_cycle, f"ads:{self.platform}")
        return c

    def _daily_cycle(self) -> None:
        for c in self.campaigns.values():
            if c.status != "ACTIVE" or c.daily_budget_usd <= 0:
                continue
            eur = to_cents(c.daily_budget_usd / self.fx)
            if not self.world.pay(eur, f"Anuncios {self.platform} ({c.name})",
                                  COUNTERPARTY[self.platform], ref=c.id):
                c.status = "PAUSED"          # sin saldo: campaña pausada, como una tarjeta rechazada
                continue
            step = funnel(self.bench, self.platform, c.category, c.daily_budget_usd,
                          prior_impressions=c.impressions, appeal=c.appeal)
            c.impressions += step["impressions"]
            c.clicks += step["clicks"]
            c.spend_usd += c.daily_budget_usd
            c.days += 1
            self.clicks_by_category[c.category] = self.clicks_by_category.get(c.category, 0.0) + step["clicks"]
        # siguiente día
        self.world.clock.schedule_in(timedelta(days=1), self._daily_cycle, f"ads:{self.platform}")

    def pause(self, cid: str) -> bool:
        c = self.campaigns.get(cid)
        if c:
            c.status = "PAUSED"
            return True
        return False

    def reach_for(self, category: str) -> float:
        return reach_from_clicks(self.bench, self.clicks_by_category.get(category, 0.0))

    def insights(self, cid: str) -> dict:
        c = self.campaigns[cid]
        clicks = c.clicks
        return {
            "impressions": str(c.impressions),
            "clicks": str(int(round(clicks))),
            "spend": f"{c.spend_usd:.2f}",
            "cpc": f"{(c.spend_usd / clicks) if clicks else 0:.2f}",
            "ctr": f"{(clicks / c.impressions * 100) if c.impressions else 0:.4f}",
            "cpm": f"{(c.spend_usd / c.impressions * 1000) if c.impressions else 0:.2f}",
            "reach": str(int(round(c.impressions * 0.7))),
        }
