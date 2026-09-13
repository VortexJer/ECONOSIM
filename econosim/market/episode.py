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
               symbols: Optional[list[str]] = None, min_day: Optional[date] = None) -> date:
    """Día de cotización de arranque con >= years_ahead años de histórico por delante.

    `min_day` pone un suelo: se usa para no arrancar antes de que las empresas publiquen
    sus cuentas, porque entonces la IA invertiría a ciegas y no aprendería a mirarlas."""
    syms = symbols or data.symbols
    days = data.trading_days(syms)
    if min_day is not None:
        days = [d for d in days if d >= min_day] or days
    if not days:
        raise RuntimeError("sin días de cotización")
    horizon = timedelta(days=int(round(years_ahead * 365.25)))
    last = days[-1]
    # candidatos: días cuyo día + horizonte no pasa del final del histórico
    eligible = [d for d in days if d + horizon <= last]
    if not eligible:
        raise ValueError(f"el histórico ({days[0]}..{last}) no cubre {years_ahead} años pedidos")
    return eligible[_seed_int(seed) % len(eligible)]


_SUELO: list = []          # caché: el cálculo lee 21 ficheros y no cambia en la vida del proceso


def suelo_con_cuentas() -> Optional[date]:
    """El primer día en que las empresas ya publican cuentas.

    Es una regla del mundo, no del arranque: un episodio anterior dejaría a la IA
    invirtiendo a ciegas, y entonces no aprende a mirar los números. Vive aquí para
    que valga igual en producción y en los tests (antes solo lo aplicaba run.py y las
    pruebas arrancaban en 2009, sin una sola cuenta publicada)."""
    if not _SUELO:
        try:
            from .fundamentals import Fundamentals
            _SUELO.append(Fundamentals().cobertura_desde())
        except Exception:
            _SUELO.append(None)
    return _SUELO[0]


def make_mask(data: MarketData, seed: str, years_ahead: float,
              symbols: Optional[list[str]] = None, min_day: Optional[date] = None) -> EpisodeMask:
    start = pick_start(data, seed, years_ahead, symbols,
                       min_day if min_day is not None else suelo_con_cuentas())
    return EpisodeMask(data, seed, start, symbols)
