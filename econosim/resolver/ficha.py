"""La ficha: forma estructurada a la que se reduce una acción de la IA."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Ficha:
    category: str
    title: str = ""
    price: Optional[float] = None            # precio unitario que pone la IA (USD)
    initial_cost: Optional[float] = None     # coste inicial declarado (None = usar el de la categoría)
    recurring_cost_monthly: Optional[float] = None
    quality: float = 5.0                     # nota del juez 0-10 (5 neutro) — la pone el juez, no la IA
    marketing_reach: float = 0.0             # alcance publicitario relativo (0 = solo orgánico)
    target_market: str = ""
    has_deliverable: bool = False            # ¿la IA produjo algo evaluable?
    raw_text: str = ""                       # lo que escribió la IA
    notes: str = ""

    def sane(self) -> "Ficha":
        """Sanea rangos: precios no negativos, calidad 0-10, reach >= 0."""
        if self.price is not None:
            self.price = max(0.0, float(self.price))
        if self.initial_cost is not None:
            self.initial_cost = max(0.0, float(self.initial_cost))
        if self.recurring_cost_monthly is not None:
            self.recurring_cost_monthly = max(0.0, float(self.recurring_cost_monthly))
        self.quality = min(10.0, max(0.0, float(self.quality)))
        self.marketing_reach = max(0.0, float(self.marketing_reach))
        return self

    def to_dict(self) -> dict:
        return asdict(self)
