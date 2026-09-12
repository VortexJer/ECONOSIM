"""ActionResolver: ata clasificador + tasas + reloj/ledger + Stripe + reputación + competencia.

Cuando la IA "hace algo" (texto libre): se clasifica, se muestrea el desenlace con
las tasas base (moduladas por reputación y competencia del nicho) y se programan
los eventos. Las VENTAS pasan por el gemelo de Stripe (comisión + payout diferido +
reembolsos/disputas); los costes fijos y las apuestas van directos al ledger.
"""
from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from typing import Optional

from ..ledger import to_cents
from ..world import World
from .classify import classify
from .ficha import Ficha
from .rates import BaseRates
from .resolve import Outcome, resolve

COUNTERPARTY = "Mercado"
MAX_CHARGES_PER_EVENT = 100        # tope de charges por evento de venta (los grandes se agrupan)
REFUND_DELAY_DAYS = 12
DISPUTE_DELAY_DAYS = 25


class ActionResolver:
    def __init__(self, world: World, rates: Optional[BaseRates] = None,
                 classifier_llm=None, classifier_model: str = "cheap",
                 stripe=None, reputation=None, competition=None,
                 judge=None, review_queue=None, ad_managers=None, hostile=None):
        self.world = world
        self.rates = rates or BaseRates()
        self.classifier_llm = classifier_llm
        self.classifier_model = classifier_model
        self.stripe = stripe                    # StripeTwin o None
        self.reputation = reputation            # Reputation o None
        self.competition = competition          # Competition o None
        self.judge = judge                      # Judge o None
        self.review_queue = review_queue        # HumanReviewQueue o None
        self.ad_managers = ad_managers or []    # lista de AdManager (meta/google)
        self.hostile = hostile                  # HostileEngine o None
        self.fx = world.load_pricing("fx")["usd_per_eur"]
        self.stripe_cfg = world.load_pricing("stripe") if stripe else None
        self.live_by_category: dict[str, int] = {}
        self.actions: list[dict] = []
        world.register("resolver", self)

    def _eur_cents(self, usd: float) -> int:
        return to_cents(usd / self.fx)

    def submit(self, text: str, action_id: str, quality: float = 5.0,
               marketing_reach: float = 0.0, has_deliverable: bool = False,
               price: Optional[float] = None, deliverable=None) -> tuple[Ficha, Outcome]:
        ficha = classify(text, self.rates, self.classifier_llm, self.classifier_model)
        # si hay entregable y juez, la CALIDAD la pone el juez (no la IA, no un parámetro)
        if deliverable is not None and self.judge is not None:
            if not deliverable.niche:
                deliverable.niche = ficha.category
            verdict = self.judge.score(deliverable)
            quality = verdict.score
            has_deliverable = True
            if self.review_queue is not None:
                self.review_queue.register(action_id, deliverable, verdict)
            ficha.notes = f"juez={verdict.score}"
        ficha.quality = quality
        # alcance = el pasado + el que aportan las campañas de anuncios vivas en la categoría
        ad_reach = sum(m.reach_for(ficha.category) for m in self.ad_managers)
        ficha.marketing_reach = marketing_reach + ad_reach
        ficha.has_deliverable = has_deliverable or ficha.has_deliverable
        if price is not None:
            ficha.price = price
        ficha.sane()
        cat = ficha.category

        # reputación -> calidad efectiva; competencia -> referencia de precio + saturación
        eff_quality = ficha.quality
        price_ref = None
        base_sat = self.live_by_category.get(cat, 0)
        if self.reputation is not None:
            eff_quality = min(10.0, max(0.0, ficha.quality + self.reputation.quality_bonus(cat)))
        if self.competition is not None:
            price_ref = self.competition.price_reference(cat)
            base_sat += self.competition.base_saturation(cat)
        sample_ficha = Ficha(**{**ficha.to_dict(), "quality": eff_quality})

        out = resolve(sample_ficha, self.rates, self.world.episode.id, action_id, base_sat, price_ref)
        self.live_by_category[cat] = self.live_by_category.get(cat, 0) + 1
        self._schedule(out, action_id, cat)
        if self.hostile is not None:
            self.hostile.on_action(cat, ficha=ficha, revenue_usd=max(0.0, out.revenue_usd))
        self.actions.append({"id": action_id, "category": cat, "title": ficha.title,
                             "units": out.units, "revenue_usd": round(out.revenue_usd, 2),
                             "eff_quality": round(eff_quality, 2), "price_ref": price_ref,
                             "detail": out.detail})
        return ficha, out

    # ---- programación de eventos ----------------------------------------
    def _schedule(self, out: Outcome, action_id: str, category: str) -> None:
        for ev in out.events:
            if ev.kind == "sale" and self.stripe is not None:
                self._schedule_sale(ev, action_id, category)
            else:
                cents = self._eur_cents(ev.amount_usd)
                if cents:
                    self._post_or_schedule(ev.day_offset, cents, ev.concept, ev.kind, action_id)

    def _schedule_sale(self, ev, action_id: str, category: str) -> None:
        # el evento agrupa `take` unidades a un precio; se reparte en charges individuales
        price_total = ev.amount_usd
        # nº de unidades del evento a partir del concepto "Venta (Nu)"
        try:
            take = int(ev.concept.split("(")[1].split("u")[0])
        except (IndexError, ValueError):
            take = 1
        unit_price = price_total / take if take else price_total
        ncharges = min(take, MAX_CHARGES_PER_EVENT)
        per = take / ncharges
        rng = self._rng(action_id, ev.day_offset)
        refund_rate = (self.stripe_cfg["refund_rate"].get(category, self.stripe_cfg["refund_rate"]["default"]))
        dispute_rate = (self.stripe_cfg["dispute_rate"].get(category, self.stripe_cfg["dispute_rate"]["default"]))

        def make_charges() -> None:
            for _ in range(ncharges):
                ch = self.stripe.record_sale(unit_price * per, "Venta", ref=action_id)
                if self.reputation is not None:
                    self.reputation.on_sale(category, n=max(1, int(round(per))))
                cid = ch["id"]
                roll = rng.random()
                if roll < dispute_rate:
                    self.world.clock.schedule_in(timedelta(days=DISPUTE_DELAY_DAYS),
                                                 lambda c=cid: self._do_refund(c, category, dispute=True),
                                                 "stripe:dispute")
                elif roll < dispute_rate + refund_rate:
                    self.world.clock.schedule_in(timedelta(days=REFUND_DELAY_DAYS),
                                                 lambda c=cid: self._do_refund(c, category, dispute=False),
                                                 "stripe:refund")

        if ev.day_offset <= 0:
            make_charges()
        else:
            self.world.clock.schedule_in(timedelta(days=ev.day_offset), make_charges, "resolver:sale")

    def _do_refund(self, charge_id: str, category: str, dispute: bool) -> None:
        r = self.stripe.refund(charge_id, dispute=dispute)
        if r and self.reputation is not None:
            (self.reputation.on_dispute if dispute else self.reputation.on_refund)(category)

    def _rng(self, action_id: str, salt) -> random.Random:
        h = hashlib.sha256(f"engine:{self.world.episode.id}:{action_id}:{salt}".encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def _post_or_schedule(self, day_offset: int, cents: int, concept: str, kind: str, ref: str) -> None:
        def apply() -> None:
            if cents >= 0:
                self.world.receive(cents, concept, COUNTERPARTY, ref=ref)
            else:
                self.world.pay(-cents, concept, COUNTERPARTY, ref=ref)
        if day_offset <= 0:
            apply()
        else:
            self.world.clock.schedule_in(timedelta(days=day_offset), apply, f"resolver:{kind}")
