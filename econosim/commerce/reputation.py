"""Reputación de la marca de la IA, por nicho. 0-100, empieza en 50 (neutra).

Sube despacio con ventas limpias, baja rápido con reembolsos y disputas (cuesta
ganar reputación y es fácil perderla, como en la realidad). Modula el resultado
futuro: más reputación -> mejor conversión.
"""
from __future__ import annotations

START = 50.0
MIN, MAX = 0.0, 100.0
SALE_GAIN = 0.4           # por venta limpia
REFUND_LOSS = 2.5         # por reembolso
DISPUTE_LOSS = 8.0        # por disputa (chargeback): mucho peor
COMPLAINT_LOSS = 1.5


class Reputation:
    def __init__(self):
        self.by_category: dict[str, float] = {}
        self.events: list[tuple[str, str, float]] = []   # (category, tipo, delta)

    def score(self, category: str) -> float:
        return self.by_category.get(category, START)

    def _adjust(self, category: str, delta: float, kind: str) -> float:
        s = min(MAX, max(MIN, self.score(category) + delta))
        self.by_category[category] = s
        self.events.append((category, kind, delta))
        return s

    def on_sale(self, category: str, n: int = 1) -> float:
        return self._adjust(category, SALE_GAIN * n, "sale")

    def on_refund(self, category: str) -> float:
        return self._adjust(category, -REFUND_LOSS, "refund")

    def on_dispute(self, category: str) -> float:
        return self._adjust(category, -DISPUTE_LOSS, "dispute")

    def on_complaint(self, category: str) -> float:
        return self._adjust(category, -COMPLAINT_LOSS, "complaint")

    def quality_bonus(self, category: str) -> float:
        """Puntos de 'calidad efectiva' que aporta la reputación al muestreo:
        50 (neutra) -> 0; 100 -> +2,5; 0 -> -2,5. Se suma a la nota del juez."""
        return (self.score(category) - START) / 20.0
