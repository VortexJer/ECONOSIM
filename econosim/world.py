"""El mundo: reloj + ledger + episodio + servicios gemelos.

Un World es un episodio: arranca con el saldo inicial y termina cuando la IA
muere (o cuando el humano lo para). Los gemelos (twins) se registran aquí y
reciben el mismo reloj y ledger.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .clock import VirtualClock, UTC
from .ledger import Ledger, InsufficientFunds, to_cents

BANK = "bank"
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Episode:
    id: str
    real_start: str            # oculto: solo motor/tests
    display_start: str
    initial_cents: int
    alive: bool = True
    death_cause: str = ""
    died_display: str = ""
    died_real: str = ""
    notes: list[str] = field(default_factory=list)


class World:
    def __init__(self, real_start: datetime, initial_eur: float = 50.0,
                 ledger_path: str = ":memory:", episode_id: Optional[str] = None,
                 pricing_dir: Path = ROOT / "data" / "pricing"):
        self.clock = VirtualClock(real_start)
        self.ledger = Ledger(ledger_path)
        self.pricing_dir = pricing_dir
        self.episode = Episode(
            id=episode_id or secrets.token_hex(4),
            real_start=self.clock.real_now().isoformat(),
            display_start=self.clock.display_iso(),
            initial_cents=to_cents(initial_eur),
        )
        self.twins: dict[str, object] = {}
        self.on_death: list[Callable[[str], None]] = []
        self.speed: float = 0.0
        self._runner: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.ledger.post(account=BANK, amount_cents=self.episode.initial_cents,
                         concept="Saldo inicial", real_ts=self.clock.real_now().isoformat(),
                         display_ts=self.clock.display_iso(), counterparty="")

    # ---- dinero ----------------------------------------------------------
    def balance(self) -> int:
        return self.ledger.balance(BANK)

    def pay(self, cents: int, concept: str, counterparty: str, ref: str = "") -> bool:
        """Cargo obligatorio. Devuelve False (sin aplicar nada) si no hay saldo."""
        try:
            self.ledger.post(account=BANK, amount_cents=-cents, concept=concept,
                             real_ts=self.clock.real_now().isoformat(),
                             display_ts=self.clock.display_iso(),
                             counterparty=counterparty, ref=ref)
            return True
        except InsufficientFunds:
            return False

    def receive(self, cents: int, concept: str, counterparty: str, ref: str = "") -> None:
        self.ledger.post(account=BANK, amount_cents=cents, concept=concept,
                         real_ts=self.clock.real_now().isoformat(),
                         display_ts=self.clock.display_iso(),
                         counterparty=counterparty, ref=ref)

    # ---- episodio --------------------------------------------------------
    def kill(self, cause: str, note: str = "") -> None:
        with self._lock:
            if not self.episode.alive:
                return
            self.episode.alive = False
            self.episode.death_cause = cause
            self.episode.died_display = self.clock.display_iso()
            self.episode.died_real = self.clock.real_now().isoformat()
            if note:
                self.episode.notes.append(note)
            self.speed = 0.0
        for cb in list(self.on_death):
            cb(cause)

    @property
    def alive(self) -> bool:
        return self.episode.alive

    def register(self, name: str, twin: object) -> None:
        self.twins[name] = twin

    def load_pricing(self, name: str) -> dict:
        return json.loads((self.pricing_dir / f"{name}.json").read_text(encoding="utf-8"))

    # ---- tiempo ----------------------------------------------------------
    def advance(self, delta: timedelta) -> int:
        return self.clock.advance(delta)

    def advance_to(self, real_dt: datetime) -> int:
        return self.clock.advance_to(real_dt)

    def run(self, speed: float = 1.0, tick: float = 0.05) -> None:
        """Avanza el reloj en segundo plano: `speed` segundos virtuales por segundo real."""
        self.speed = speed
        if self._runner and self._runner.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            last = time.monotonic()
            while not self._stop.is_set():
                time.sleep(tick)
                now = time.monotonic()
                elapsed, last = now - last, now
                if self.speed > 0 and self.alive:
                    with self._lock:
                        self.clock.advance(timedelta(seconds=elapsed * self.speed))

        self._runner = threading.Thread(target=loop, name="econosim-clock", daemon=True)
        self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        if self._runner:
            self._runner.join(timeout=2)

    # ---- estado (para el panel humano; NO para la IA) ----------------------
    def state(self, include_real: bool = False) -> dict:
        d = {
            "episode": {k: v for k, v in asdict(self.episode).items()
                        if include_real or k not in ("real_start", "died_real")},
            "display_now": self.clock.display_iso(),
            "balance_cents": self.balance(),
            "speed": self.speed,
            "alive": self.alive,
            "pending_events": [(self.clock.display(w).isoformat(), l) for w, l in self.clock.pending()[:20]],
        }
        if include_real:
            d["real_now"] = self.clock.real_now().isoformat()
        return d
