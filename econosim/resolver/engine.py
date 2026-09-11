"""ActionResolver: ata clasificador + tasas + reloj/ledger del mundo.

Cuando la IA "hace algo" (texto libre), esto lo clasifica, muestrea el desenlace
con las tasas base y programa los eventos (costes ya, ventas/cobros en el futuro)
en el ledger. Lleva la cuenta de la saturación por categoría (acciones vivas).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..ledger import to_cents
from ..world import World
from .classify import classify
from .ficha import Ficha
from .rates import BaseRates
from .resolve import Outcome, resolve

COUNTERPARTY = "Mercado"


class ActionResolver:
    def __init__(self, world: World, rates: Optional[BaseRates] = None,
                 classifier_llm=None, classifier_model: str = "cheap"):
        self.world = world
        self.rates = rates or BaseRates()
        self.classifier_llm = classifier_llm
        self.classifier_model = classifier_model
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.live_by_category: dict[str, int] = {}
        self.actions: list[dict] = []
        world.register("resolver", self)

    def _eur_cents(self, usd: float) -> int:
        return to_cents(usd / self.fx)

    def submit(self, text: str, action_id: str, quality: float = 5.0,
               marketing_reach: float = 0.0, has_deliverable: bool = False,
               price: Optional[float] = None) -> tuple[Ficha, Outcome]:
        ficha = classify(text, self.rates, self.classifier_llm, self.classifier_model)
        # la calidad la pone el juez (fase 6); aquí entra como parámetro. Idem marketing.
        ficha.quality = quality
        ficha.marketing_reach = marketing_reach
        ficha.has_deliverable = has_deliverable or ficha.has_deliverable
        if price is not None:
            ficha.price = price
        ficha.sane()

        saturation = self.live_by_category.get(ficha.category, 0)
        out = resolve(ficha, self.rates, self.world.episode.id, action_id, saturation)
        self.live_by_category[ficha.category] = saturation + 1
        self._schedule(out, action_id)
        self.actions.append({"id": action_id, "category": ficha.category, "title": ficha.title,
                             "units": out.units, "revenue_usd": round(out.revenue_usd, 2),
                             "detail": out.detail})
        return ficha, out

    def _schedule(self, out: Outcome, action_id: str) -> None:
        for ev in out.events:
            cents = self._eur_cents(ev.amount_usd)
            if cents == 0:
                continue
            self._post_or_schedule(ev.day_offset, cents, ev.concept, ev.kind, action_id)

    def _post_or_schedule(self, day_offset: int, cents: int, concept: str, kind: str, ref: str) -> None:
        def apply() -> None:
            if cents >= 0:
                self.world.receive(cents, concept, COUNTERPARTY, ref=ref)
            else:
                self.world.pay(-cents, concept, COUNTERPARTY, ref=ref)   # si no hay saldo, no se aplica
        if day_offset <= 0:
            apply()
        else:
            self.world.clock.schedule_in(timedelta(days=day_offset), apply, f"resolver:{kind}")
