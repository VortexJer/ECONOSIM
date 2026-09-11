"""Muestreo del desenlace y calendario de eventos. Los euros salen de aquí, no del LLM.

Modelo de venta (categorías con `units_lognormal`):
  * masa en 0 con prob p_zero (ajustada por calidad y marketing).
  * si no es 0, unidades ~ Lognormal(mediana, sigma) -> cola pesada: casi todo cerca
    de la mediana, una minoría muy arriba. Se modula por calidad, precio (elasticidad),
    marketing y saturación del nicho.
Apuestas (`negative_ev`): resultado de EV negativo y varianza alta.

Todo es determinista dada (semilla de episodio, id de acción): reproducible.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .ficha import Ficha
from .rates import BaseRates, Category


@dataclass
class Event:
    day_offset: int      # días desde el arranque de la acción
    amount_usd: float    # + ingreso, - coste
    concept: str
    kind: str            # "initial_cost" | "recurring_cost" | "sale" | "bet"


@dataclass
class Outcome:
    category: str
    units: int = 0
    revenue_usd: float = 0.0
    events: list[Event] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _rng(seed: str, action_id: str) -> random.Random:
    h = hashlib.sha256(f"resolve:{seed}:{action_id}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _adjusted_pzero(cat: Category, mod: dict, ficha: Ficha) -> float:
    p0 = float(cat.get("p_zero", 0.5))
    p0 += (ficha.quality - 5.0) * mod["quality"]["pzero_per_point"]
    if ficha.marketing_reach > 0:
        p0 -= mod["marketing"]["pzero_reach_drop"] * (ficha.marketing_reach / (1.0 + ficha.marketing_reach))
    return min(0.98, max(0.02, p0))


def _unit_multiplier(cat: Category, mod: dict, ficha: Ficha, saturation: int,
                     price_ref: Optional[float] = None) -> float:
    m = 1.0
    m *= math.exp((ficha.quality - 5.0) * mod["quality"]["units_per_point"])
    if ficha.marketing_reach > 0:
        m *= math.exp(mod["marketing"]["units_log_gain"] * math.log1p(ficha.marketing_reach))
    # elasticidad contra el precio de referencia del nicho (competencia) o la mediana de la tabla
    ref = price_ref if price_ref else cat.get("median_price")
    price = ficha.price if ficha.price is not None else ref
    if price and ref:
        m *= (price / ref) ** cat.get("price_elasticity", 0.0)
    m *= 1.0 / (1.0 + saturation / float(mod["saturation"]["units_half_at"]))
    return m


def expected_units(rates: BaseRates, ficha: Ficha, saturation: int = 0,
                   price_ref: Optional[float] = None) -> float:
    """Unidades esperadas E[U] (para validar el muestreo). Media lognormal = mediana·e^(σ²/2)."""
    cat = rates[ficha.category]
    ln = cat.get("units_lognormal")
    if not ln:
        return 0.0
    p0 = _adjusted_pzero(cat, rates.modulation, ficha)
    mean_ln = ln["median"] * math.exp(ln["sigma"] ** 2 / 2.0)
    return (1.0 - p0) * mean_ln * _unit_multiplier(cat, rates.modulation, ficha, saturation, price_ref)


def resolve(ficha: Ficha, rates: BaseRates, seed: str, action_id: str,
            saturation: int = 0, price_ref: Optional[float] = None) -> Outcome:
    ficha = ficha.sane()
    cat = rates[ficha.category]
    rng = _rng(seed, action_id)
    out = Outcome(category=ficha.category)

    # --- costes: inicial ya, recurrente mensual durante la ventana ----------
    ic = ficha.initial_cost if ficha.initial_cost is not None else cat.get("initial_cost", 0.0)
    rc = ficha.recurring_cost_monthly if ficha.recurring_cost_monthly is not None else cat.get("recurring_cost_monthly", 0.0)
    window = int(cat.get("sales_window_days", 90))
    if ic and ic > 0:
        out.events.append(Event(0, -ic, "Coste inicial", "initial_cost"))
    if rc and rc > 0:
        for k in range(0, max(1, window // 30) + 1):
            out.events.append(Event(k * 30, -rc, "Coste recurrente", "recurring_cost"))

    # --- apuestas: EV negativo, varianza alta -------------------------------
    if cat.get("negative_ev"):
        stake = ficha.price if ficha.price and ficha.price > 0 else 10.0
        margin = float(cat.get("house_margin", 0.05))
        # cuota justa 2.0 -> con margen la casa paga menos; prob de ganar ~ 0.5
        p_win = 0.5
        payout = (2.0 * (1.0 - margin)) if rng.random() < p_win else 0.0   # gana ~2x-margen o pierde todo
        pnl = stake * payout - stake
        out.revenue_usd = pnl
        out.events.append(Event(int(cat.get("first_result_days", 1)), pnl,
                                "Resultado de apuesta", "bet"))
        out.detail = {"stake": stake, "won": payout > 0, "house_margin": margin}
        return out

    # --- venta: masa en cero + lognormal modulada ---------------------------
    ln = cat.get("units_lognormal")
    if ln:
        p0 = _adjusted_pzero(cat, rates.modulation, ficha)
        if rng.random() < p0:
            units = 0
        else:
            mu = math.log(ln["median"])
            draw = math.exp(rng.gauss(mu, ln["sigma"]))
            units = max(0, int(round(draw * _unit_multiplier(cat, rates.modulation, ficha, saturation, price_ref))))
        price = ficha.price if ficha.price is not None else cat.get("median_price", 0.0)
        out.units = units
        out.revenue_usd = units * price
        # repartir las ventas en la ventana, con perfil decreciente desde el primer resultado
        if units > 0 and price > 0:
            start = int(cat.get("first_result_days", 2))
            span = max(1, window - start)
            remaining = units
            day = start
            while remaining > 0 and day < window + start:
                # más ventas al principio: fracción geométrica
                take = max(1, int(round(remaining * 0.35)))
                take = min(take, remaining)
                out.events.append(Event(day, take * price, f"Venta ({take}u)", "sale"))
                remaining -= take
                day += max(1, span // 6)
            if remaining > 0:
                out.events.append(Event(window + start, remaining * price, f"Venta ({remaining}u)", "sale"))
        out.detail = {"p_zero": round(p0, 3), "price": price,
                      "expected_units": round(expected_units(rates, ficha, saturation, price_ref), 2)}
    return out
