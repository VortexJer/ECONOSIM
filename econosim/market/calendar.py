"""Calendario y reloj de mercado NYSE: sesión 9:30–16:00 ET, findes y festivos cerrados.

Trabaja en fechas reales (las del histórico). El horario real se conserva; el año
lo desplaza el reloj del mundo al mostrarlo. Los festivos se derivan de los propios
datos: si NINGÚN símbolo cotizó ese día laborable, es festivo.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

# NYSE en hora del Este. Usamos un offset fijo UTC-4 (EDT) para el open/close;
# suficiente para el simulador (no modelamos el cambio EST/EDT al detalle).
MARKET_OPEN = time(13, 30)      # 09:30 ET  ≈ 13:30 UTC (EDT)
MARKET_CLOSE = time(20, 0)      # 16:00 ET  ≈ 20:00 UTC (EDT)
UTC = timezone.utc


class MarketCalendar:
    def __init__(self, trading_days: list[date]):
        self._days = trading_days
        self._set = set(trading_days)

    def is_trading_day(self, d: date) -> bool:
        return d in self._set

    def is_open(self, dt: datetime) -> bool:
        d = dt.astimezone(UTC)
        return self.is_trading_day(d.date()) and MARKET_OPEN <= d.timetz().replace(tzinfo=None) < MARKET_CLOSE

    def session_bounds(self, d: date) -> tuple[datetime, datetime]:
        return (datetime.combine(d, MARKET_OPEN, UTC), datetime.combine(d, MARKET_CLOSE, UTC))

    def next_open(self, dt: datetime) -> datetime:
        d = dt.astimezone(UTC)
        day = d.date()
        # hoy aún no ha abierto
        if self.is_trading_day(day) and d.timetz().replace(tzinfo=None) < MARKET_OPEN:
            return datetime.combine(day, MARKET_OPEN, UTC)
        day = day + timedelta(days=1)
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return datetime.combine(day, MARKET_OPEN, UTC)

    def next_close(self, dt: datetime) -> datetime:
        d = dt.astimezone(UTC)
        day = d.date()
        if self.is_trading_day(day) and d.timetz().replace(tzinfo=None) < MARKET_CLOSE:
            return datetime.combine(day, MARKET_CLOSE, UTC)
        no = self.next_open(dt)
        return datetime.combine(no.date(), MARKET_CLOSE, UTC)

    def session_asof(self, d: date) -> Optional[date]:
        """El día de sesión de `d`, o el último anterior si `d` no cotiza."""
        from bisect import bisect_right
        i = bisect_right(self._days, d)
        return self._days[i - 1] if i else None

    def sessions_between(self, start: date, end: date) -> list[date]:
        return [d for d in self._days if start <= d <= end]
