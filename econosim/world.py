"""El mundo: reloj + ledger + episodio + servicios gemelos.

Un World es un episodio: arranca con el saldo inicial y termina cuando la IA
muere (o cuando el humano lo para). Los gemelos (twins) se registran aquí y
reciben el mismo reloj y ledger.
"""
from __future__ import annotations

import os

import json
import secrets
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .clock import VirtualClock, UTC, OFFSET_YEARS
from .ledger import Ledger, InsufficientFunds, to_cents
from .live import LiveGuard

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
    # Un episodio TERMINA por muerte (sin dinero) o por llegar al horizonte pedido.
    # Dormir (end_session) nunca termina nada: la vida sigue.
    ended: bool = False
    end_cause: str = ""        # "death" | "horizon"
    ended_display: str = ""
    notes: list[str] = field(default_factory=list)


class World:
    def __init__(self, real_start: datetime, initial_eur: float = 50.0,
                 ledger_path: str = ":memory:", episode_id: Optional[str] = None,
                 pricing_dir: Path = ROOT / "data" / "pricing",
                 offset_years: int = OFFSET_YEARS, live: bool = False,
                 sim_duration: Optional[timedelta] = None,
                 idle_speed: float = 0.0, active_grace_s: float = 15.0,
                 active_speed: float = 1.0):
        self.clock = VirtualClock(real_start, offset_years=offset_years)
        # candado de egreso del modo en vivo: ninguna acción sale (PROYECTO.md §2.6).
        self.live = LiveGuard(enabled=live, now=lambda: self.clock.display_iso())
        self.ledger = Ledger(ledger_path)
        if self.ledger.entries():
            raise RuntimeError(f"el ledger {ledger_path} ya tiene asientos: un episodio nunca reutiliza el ledger de otro")
        self.pricing_dir = pricing_dir
        self.episode = Episode(
            id=episode_id or secrets.token_hex(4),
            real_start=self.clock.real_now().isoformat(),
            display_start=self.clock.display_iso(),
            initial_cents=to_cents(initial_eur),
        )
        self.twins: dict[str, object] = {}
        self.on_death: list[Callable[[str], None]] = []
        self.on_end: list[Callable[[str], None]] = []    # fin del episodio (muerte u horizonte)
        self.on_tick: list[Callable[[], None]] = []      # p.ej. publicar el desfase horario al sandbox
        # Horizonte: el episodio dura lo que se pida (1 día, 1 mes, 1 año...). None = sin límite.
        self.sim_end_real: Optional[datetime] = (self.clock.real_now() + sim_duration) if sim_duration else None
        # Reloj consciente de actividad: ×1 mientras la IA actúa (pensar cuesta segundos
        # reales, como en la realidad) y `idle_speed` mientras duerme (comprime la espera).
        # 0 = modelo clásico de velocidad constante. La IA NO controla esto: se infiere de
        # sus peticiones a los gemelos, así que no hay canal que pueda manipular.
        # La gracia (15 s) absorbe los huecos normales ENTRE pasos de una sesión (llamada al
        # LLM -> bash -> siguiente llamada); con 2 s cada microhueco se inflaba a horas, y con
        # 60 s cada siesta corta costaba un minuto real antes de comprimirse.
        self.idle_speed = idle_speed
        # Velocidad mientras la IA trabaja. TIENE QUE SER ×1, y no es una manía de purista:
        # el reloj del contenedor de la IA es ESTE reloj (libfaketime). Al acelerarlo, cada
        # plazo dentro de la máquina se multiplica: con ×60, dos segundos reales son 119
        # para ella, así que `curl -m 10` muere a los 0,17 s y su propio vigilante mata todo
        # antes de que llegue una respuesta. Lo probamos: la IA se pasó cinco meses sin poder
        # hablar con el bróker y murió sin comprar una sola acción. Solo se comprime la
        # ESPERA (idle_speed), que ahí no hay ninguna petición viva que romper.
        if active_speed != 1.0 and os.environ.get("ECONOSIM_ACTIVE_SPEED_FORZAR", "") in ("", "0", "false"):
            raise ValueError(
                f"active_speed={active_speed:g} rompe los plazos dentro del contenedor de la IA "
                "(su reloj es este). Para ir rápido, comprime la espera con idle_speed y deja que "
                "la IA duerma; si de verdad quieres arriesgarte, ECONOSIM_ACTIVE_SPEED_FORZAR=1.")
        self.active_speed = active_speed
        self.active_grace_s = active_grace_s
        self._last_activity = 0.0
        self._inflight = 0
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

    # ---- actividad de la IA (para el reloj consciente de actividad) --------
    def activity_begin(self) -> None:
        """Entra una petición de la IA a un gemelo: está trabajando."""
        with self._lock:
            self._inflight += 1
        self._last_activity = time.monotonic()

    def activity_end(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
        self._last_activity = time.monotonic()

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _is_active(self) -> bool:
        """Activa si hay algo en vuelo (p.ej. el LLM generando, que puede tardar un
        minuto) o si acaba de hacer algo (gracia entre pasos de una sesión)."""
        return self._inflight > 0 or (time.monotonic() - self._last_activity) < self.active_grace_s

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
        self.finish("death")

    def finish(self, cause: str) -> None:
        """Termina el episodio SIN que sea necesariamente muerte (p.ej. se acabó el
        horizonte pedido y la IA sobrevivió). Dormir nunca llama aquí."""
        with self._lock:
            if self.episode.ended:
                return
            self.episode.ended = True
            self.episode.end_cause = cause
            self.episode.ended_display = self.clock.display_iso()
            self.speed = 0.0
        for cb in list(self.on_end):
            cb(cause)

    def _check_horizon(self) -> None:
        if self.sim_end_real is not None and not self.episode.ended \
                and self.clock.real_now() >= self.sim_end_real:
            self.finish("horizon")

    @property
    def alive(self) -> bool:
        return self.episode.alive

    def register(self, name: str, twin: object) -> None:
        self.twins[name] = twin

    def load_pricing(self, name: str) -> dict:
        return json.loads((self.pricing_dir / f"{name}.json").read_text(encoding="utf-8"))

    # ---- tiempo ----------------------------------------------------------
    def advance(self, delta: timedelta) -> int:
        return self.advance_to(self.clock.real_now() + delta)

    def advance_to(self, real_dt: datetime) -> int:
        """Avanza, sin pasar nunca del horizonte pedido."""
        if self.sim_end_real is not None and real_dt > self.sim_end_real:
            real_dt = self.sim_end_real
        ran = self.clock.advance_to(real_dt)
        self._check_horizon()
        return ran

    def run(self, speed: float = 1.0, tick: float = 0.05) -> None:
        """Avanza el reloj en segundo plano.

        Con `idle_speed` > 0 el reloj es *consciente de actividad*: va a ×1 mientras la
        IA actúa (pensar y trabajar cuestan segundos reales, como en la realidad) y a
        `idle_speed` mientras duerme, comprimiendo solo la espera. Sin él, velocidad
        constante `speed` (modelo clásico, usado por los tests)."""
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
                if self.idle_speed > 0:
                    # pensar/trabajar a ×1; dormir, comprimido
                    self.speed = self.active_speed if self._is_active() else self.idle_speed
                if self.speed > 0 and self.alive and not self.episode.ended:
                    with self._lock:
                        self.advance(timedelta(seconds=elapsed * self.speed))
                for cb in self.on_tick:
                    cb()

        self._runner = threading.Thread(target=loop, name="econosim-clock", daemon=True)
        self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        if self._runner:
            self._runner.join(timeout=2)

    def faketime_offset(self) -> float:
        """Segundos que hay que sumar al reloj real del host para ver la fecha mostrada."""
        return self.clock.display_now.timestamp() - time.time()

    # ---- estado (para el panel humano; NO para la IA) ----------------------
    def state(self, include_real: bool = False) -> dict:
        d = {
            "episode": {k: v for k, v in asdict(self.episode).items()
                        if include_real or k not in ("real_start", "died_real")},
            "display_now": self.clock.display_iso(),
            "balance_cents": self.balance(),
            "speed": self.speed,
            "alive": self.alive,
            "ended": self.episode.ended,
            "end_cause": self.episode.end_cause,
            "sim_end_display": self.clock.display_iso(self.sim_end_real) if self.sim_end_real else None,
            "live": self.live.snapshot(),
            "pending_events": [(self.clock.display(w).isoformat(), l) for w, l in self.clock.pending()[:20]],
        }
        if include_real:
            d["real_now"] = self.clock.real_now().isoformat()
        return d
