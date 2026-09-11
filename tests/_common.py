"""Utilidades de test: mundo en memoria + internet falso en un puerto libre."""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from econosim.clock import UTC  # noqa: E402
from econosim.fakenet.server import FakeNet  # noqa: E402
from econosim.twins.hetzner import HetznerTwin  # noqa: E402
from econosim.world import World  # noqa: E402

REAL_START = datetime(1998, 10, 14, 9, 30, tzinfo=UTC)   # miércoles; +28 = 2026-10-14, miércoles


def check(cond: bool, msg: str) -> None:
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def make_world(real_start: datetime = REAL_START, initial_eur: float = 50.0) -> tuple[World, HetznerTwin]:
    w = World(real_start, initial_eur=initial_eur)
    h = HetznerTwin(w, token="test-token")
    return w, h


class LiveNet:
    """FakeNet corriendo en un hilo con su propio loop, en 127.0.0.1:puerto libre."""

    def __init__(self, fakenet: FakeNet):
        self.fakenet = fakenet
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def boot() -> None:
            await self.fakenet.start("127.0.0.1", 0)
            self.port = self.fakenet.bound_ports()[0]
            self._ready.set()

        self._loop.run_until_complete(boot())
        self._loop.run_forever()

    def __enter__(self) -> "LiveNet":
        self._thread.start()
        self._ready.wait(5)
        return self

    def __exit__(self, *exc) -> None:
        asyncio.run_coroutine_threadsafe(self.fakenet.stop(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"
