"""Competencia por nicho: rivales que ya existen, con precio y calidad.

Deterministas dada la semilla del episodio: cada categoría tiene un puñado de
rivales cuyo precio orbita el precio mediano de la tabla y cuya calidad varía.
Fijan (a) la referencia de precio real del nicho — contra la que se mide si la
IA está cara o barata — y (b) la saturación de base (más rivales, más difícil).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from ..resolver.rates import BaseRates


@dataclass(frozen=True)
class Rival:
    name: str
    price: float
    quality: float          # 0-10


def _rng(seed: str, category: str) -> random.Random:
    h = hashlib.sha256(f"competition:{seed}:{category}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


class Competition:
    def __init__(self, rates: BaseRates, seed: str):
        self.rates = rates
        self.seed = seed
        self._rivals: dict[str, list[Rival]] = {}

    def rivals(self, category: str) -> list[Rival]:
        if category not in self._rivals:
            self._rivals[category] = self._make(category)
        return self._rivals[category]

    def _make(self, category: str) -> list[Rival]:
        cat = self.rates.get(category)
        if cat is None or not cat.get("median_price"):
            return []
        rng = _rng(self.seed, category)
        n = rng.randint(4, 12)                     # cuántos rivales ya están en el nicho
        base = cat["median_price"]
        out = []
        for i in range(n):
            price = round(base * math_lognormal(rng, 0.0, 0.35), 2)   # orbita el precio mediano
            quality = min(10.0, max(1.0, rng.gauss(6.0, 1.8)))
            out.append(Rival(f"{category[:3].upper()}-riv{i+1}", max(0.5, price), round(quality, 1)))
        return out

    def price_reference(self, category: str) -> float:
        """Precio contra el que se mide la IA: la mediana de los rivales (o el de la tabla)."""
        rv = self.rivals(category)
        if not rv:
            cat = self.rates.get(category)
            return cat["median_price"] if cat and cat.get("median_price") else 0.0
        prices = sorted(r.price for r in rv)
        return prices[len(prices) // 2]

    def base_saturation(self, category: str) -> int:
        return len(self.rivals(category))

    def avg_quality(self, category: str) -> float:
        rv = self.rivals(category)
        return sum(r.quality for r in rv) / len(rv) if rv else 5.0


def math_lognormal(rng: random.Random, mu: float, sigma: float) -> float:
    import math
    return math.exp(rng.gauss(mu, sigma))
