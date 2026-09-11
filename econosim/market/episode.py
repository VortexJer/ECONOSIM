"""Arranque aleatorio de episodio: elige un punto del histórico con margen suficiente.

Dada una semilla y N años pedidos, elige un día de arranque tal que queden >= N años
de datos por delante. Mismo seed -> mismo arranque y mismos alias (reproducible);
seeds distintas -> puntos y renombrados distintos (robustez frente a regímenes).
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Optional

from .data import MarketData
from .mask import EpisodeMask


def _seed_int(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(f"episode:{seed}".encode()).digest()[:8], "big")


def pick_start(data: MarketData, seed: str, years_ahead: float,
               symbols: Optional[list[str]] = None) -> date:
    """Día de cotización de arranque con >= years_ahead años de histórico por delante."""
    syms = symbols or data.symbols
    days = data.trading_days(syms)
    if not days:
        raise RuntimeError("sin días de cotización")
    horizon = timedelta(days=int(round(years_ahead * 365.25)))
    last = days[-1]
    # candidatos: días cuyo día + horizonte no pasa del final del histórico
    eligible = [d for d in days if d + horizon <= last]
    if not eligible:
        raise ValueError(f"el histórico ({days[0]}..{last}) no cubre {years_ahead} años pedidos")
    return eligible[_seed_int(seed) % len(eligible)]


def make_mask(data: MarketData, seed: str, years_ahead: float,
              symbols: Optional[list[str]] = None) -> EpisodeMask:
    start = pick_start(data, seed, years_ahead, symbols)
    return EpisodeMask(data, seed, start, symbols)
