"""Reloj virtual con máscara temporal.

El mundo lleva dos fechas:
  * `real`: la fecha real de los datos históricos. Oculta. Solo la usan el
    motor y los tests.
  * `display`: la que ve la IA. Es `real` desplazada exactamente
    OFFSET_YEARS (28) años: el ciclo gregoriano completo, así que mes, día,
    día de la semana y festividades coinciden. Ver PLAN.md.

Los eventos se programan en fecha real y se ejecutan en orden estricto; el
reloj avanza por saltos (tests, aceleración) o en tiempo real (World.run).
"""
from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

OFFSET_YEARS = 28
UTC = timezone.utc


def shift_years(dt: datetime, years: int) -> datetime:
    # Con un múltiplo de 28 años el 29 de febrero siempre existe en destino.
    return dt.replace(year=dt.year + years)


@dataclass(order=True)
class _Event:
    when: datetime
    seq: int
    callback: Callable[[], None] = field(compare=False)
    label: str = field(compare=False, default="")
    cancelled: bool = field(compare=False, default=False)


class VirtualClock:
    def __init__(self, real_start: datetime, offset_years: int = OFFSET_YEARS):
        if real_start.tzinfo is None:
            real_start = real_start.replace(tzinfo=UTC)
        self._real_now = real_start
        self.offset_years = offset_years
        self._heap: list[_Event] = []
        self._seq = itertools.count()
        self._lock = threading.RLock()
        self.processed = 0

    # ---- lectura ---------------------------------------------------------
    def real_now(self) -> datetime:
        """Fecha real de los datos. NUNCA exponer a la IA."""
        return self._real_now

    @property
    def display_now(self) -> datetime:
        return shift_years(self._real_now, self.offset_years)

    def display(self, real_dt: datetime) -> datetime:
        return shift_years(real_dt, self.offset_years)

    def display_iso(self, real_dt: Optional[datetime] = None) -> str:
        dt = self.display(real_dt) if real_dt is not None else self.display_now
        return dt.astimezone(UTC).replace(microsecond=0).isoformat()

    # ---- eventos ---------------------------------------------------------
    def schedule(self, when: datetime, callback: Callable[[], None], label: str = "") -> _Event:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        with self._lock:
            ev = _Event(when, next(self._seq), callback, label)
            heapq.heappush(self._heap, ev)
            return ev

    def schedule_in(self, delta: timedelta, callback: Callable[[], None], label: str = "") -> _Event:
        return self.schedule(self._real_now + delta, callback, label)

    @staticmethod
    def cancel(ev: _Event) -> None:
        ev.cancelled = True

    def next_event_time(self) -> Optional[datetime]:
        with self._lock:
            while self._heap and self._heap[0].cancelled:
                heapq.heappop(self._heap)
            return self._heap[0].when if self._heap else None

    def pending(self) -> list[tuple[datetime, str]]:
        with self._lock:
            return sorted((e.when, e.label) for e in self._heap if not e.cancelled)

    # ---- avance ----------------------------------------------------------
    def advance_to(self, target: datetime) -> int:
        """Avanza hasta `target` ejecutando cada evento vencido en su instante."""
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        ran = 0
        with self._lock:
            if target < self._real_now:
                raise ValueError("el reloj no retrocede")
            while self._heap and self._heap[0].when <= target:
                ev = heapq.heappop(self._heap)
                if ev.cancelled:
                    continue
                self._real_now = ev.when
                ev.callback()
                ran += 1
            self._real_now = target
        self.processed += ran
        return ran

    def advance(self, delta: timedelta) -> int:
        return self.advance_to(self._real_now + delta)
