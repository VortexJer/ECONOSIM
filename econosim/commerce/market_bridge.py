"""Puente de mercado: cierra el bucle producto -> ventas.

Cuando la IA crea un precio en Stripe para un producto, esto registra un 'listing'
(categoría, precio, contenido) y programa que el mundo lo resuelva periódicamente:
el juez puntúa el contenido -> calidad; el resolutor muestrea ventas (con competencia,
reputación y alcance de anuncios) y las cobra por Stripe. Así una tienda 'viva' vende
sola con el tiempo, sin que el motor enumere actividades (§2.1).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..judge.judge import Deliverable
from ..world import World

RESOLVE_EVERY_DAYS = 14
MAX_CYCLES = 26            # ~1 año de ciclos quincenales


class MarketBridge:
    def __init__(self, world: World, resolver, stripe, judge=None):
        self.world = world
        self.resolver = resolver
        self.stripe = stripe
        self.judge = judge
        self.listings: dict[str, dict] = {}       # price_id -> listing
        stripe.on_price = self._on_price
        world.register("market_bridge", self)

    def _on_price(self, product: Optional[dict], price: dict) -> None:
        if not product:
            return
        meta = product.get("metadata", {}) or {}
        category = meta.get("category") or "digital_product"
        content = product.get("description", "") or product.get("name", "")
        listing_id = price["id"]
        if listing_id in self.listings:
            return
        # el juez puntúa el contenido del producto UNA vez -> calidad de la tienda
        quality = 5.0
        if self.judge is not None:
            quality = self.judge.score(Deliverable(kind="producto", content=content, niche=category)).score
        listing = {"id": listing_id, "product": product["id"], "category": category,
                   "price_usd": price["unit_amount"] / 100.0, "content": content,
                   "quality": quality, "cycles": 0, "active": True, "units": 0}
        self.listings[listing_id] = listing
        self._schedule(listing)

    def _schedule(self, listing: dict) -> None:
        self.world.clock.schedule_in(timedelta(days=RESOLVE_EVERY_DAYS),
                                     lambda: self._resolve(listing), "market:resolve")

    def _resolve(self, listing: dict) -> None:
        if not listing["active"] or not self.world.alive or listing["cycles"] >= MAX_CYCLES:
            listing["active"] = False
            return
        listing["cycles"] += 1
        aid = f"{listing['id']}-c{listing['cycles']}"
        deliverable = Deliverable(kind="producto", content=listing["content"], niche=listing["category"])
        # el resolutor: juez -> calidad, competencia, reputación, alcance de anuncios; ventas por Stripe
        _, out = self.resolver.submit(listing["content"] or "producto", aid,
                                      price=listing["price_usd"], has_deliverable=True,
                                      deliverable=deliverable if self.judge else None,
                                      quality=listing["quality"])
        listing["units"] += out.units
        self._schedule(listing)

    def summary(self) -> list[dict]:
        return [{"id": l["id"], "category": l["category"], "price_usd": l["price_usd"],
                 "quality": round(l["quality"], 2), "cycles": l["cycles"], "units": l["units"],
                 "active": l["active"]} for l in self.listings.values()]
