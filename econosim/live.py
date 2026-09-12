"""Modo en vivo (PROYECTO.md §2.6, §16.13): datos reales entran, ninguna acción sale.

Es el ensayo general antes de la fase posterior (fuera de este proyecto) en la que
el DNS se apunta a las APIs reales. En vivo:

  * **Datos reales entran**: el mercado deja de enmascararse. `LiveMask` es la
    identidad — símbolos, precios y fechas REALES, sin alias, sin indexar a 100,
    sin desplazar el tiempo 28 años. La IA ve la realidad tal cual.
  * **Ninguna acción sale**: `LiveGuard` es un candado de egreso. Toda operación
    con efecto externo (colocar una orden, cobrar, enviar correo, registrar un
    dominio, apostar, aprovisionar un servidor, gastar en anuncios) se intercepta,
    se anota en el diario de egreso y se devuelve una respuesta benigna SIN mutar
    el mundo. Las LECTURAS (datos de mercado, saldos, bandeja) pasan siempre.

El candado vive en el mismo punto que, al apuntar el DNS a lo real, sería el
único sitio por donde algo tocaría el mundo. Por eso se prueba aquí.
"""
from __future__ import annotations

from typing import Callable, Optional

from .market.data import MarketData
from .market.mask import EpisodeMask


class LiveGuard:
    """Candado de egreso. Desactivado (por defecto) no hace nada: el sandbox opera
    normal. Activado, `block()` anota el intento y devuelve True para que el gemelo
    responda benigno sin ejecutar la acción."""

    def __init__(self, enabled: bool = False, now: Optional[Callable[[], str]] = None):
        self.enabled = bool(enabled)
        self._now = now or (lambda: "")
        self.journal: list[dict] = []
        self._seq = 0

    def block(self, service: str, op: str, detail: Optional[dict] = None) -> bool:
        """¿Bloquear esta acción de egreso? Si el modo en vivo está activo, la anota
        y devuelve True. Si no, devuelve False y el gemelo sigue su curso normal."""
        if not self.enabled:
            return False
        self._seq += 1
        self.journal.append({
            "seq": self._seq,
            "ts": self._now(),
            "service": service,
            "op": op,
            "detail": detail or {},
        })
        return True

    @property
    def blocked_count(self) -> int:
        return self._seq

    def snapshot(self, limit: int = 30) -> dict:
        return {"enabled": self.enabled, "blocked": self._seq, "journal": self.journal[-limit:]}


class LiveMask(EpisodeMask):
    """Máscara identidad para el modo en vivo: datos de mercado REALES.

    No renombra símbolos, no indexa precios, no desplaza fechas. El episodio se
    sitúa en el último día real disponible (el borde del histórico = "hoy" en una
    validación en vivo). Comparte la interfaz de `EpisodeMask` para que el gemelo
    de Alpaca no note la diferencia: solo que ahora lo que sirve es la realidad."""

    def __init__(self, data: MarketData, symbols: Optional[list[str]] = None):
        syms = symbols or data.symbols
        days = data.trading_days(syms)
        if not days:
            raise RuntimeError("sin días de cotización para el modo en vivo")
        self.data = data
        self.seed = "live"
        self.start_day = days[-1]                 # el último día real = "hoy"
        self.real_symbols = syms
        self.alias_of = {s: s for s in syms}      # identidad: el alias ES el símbolo real
        self.real_of = {s: s for s in syms}
        self._factor = {s: 1.0 for s in syms}     # sin indexar: precios reales
