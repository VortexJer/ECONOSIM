"""G1: eventos en orden a cualquier velocidad; fecha mostrada = real + 28 años, mismo weekday."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta

from _common import REAL_START, check
from econosim.clock import UTC, VirtualClock, OFFSET_YEARS
from econosim.world import World

# --- máscara ---------------------------------------------------------------
c = VirtualClock(REAL_START)
d = c.display_now
check(d.year == REAL_START.year + OFFSET_YEARS, f"año mostrado {d.year}")
check((d.month, d.day, d.hour, d.minute) == (10, 14, 9, 30), "mes/día/hora se conservan")
check(d.weekday() == REAL_START.weekday(), "día de la semana se conserva (+28 años)")
check("1998" not in c.display_iso() and c.display_iso().startswith("2026-10-14T09:30:00+00:00"), c.display_iso())

# 29 de febrero sobrevive al desplazamiento en ambos sentidos del ciclo
leap = VirtualClock(datetime(2000, 2, 29, tzinfo=UTC))
check(leap.display_now.month == 2 and leap.display_now.day == 29, "29-feb se conserva")

# el weekday se conserva a lo largo de 20 años de fechas aleatorias
rnd = random.Random(7)
for _ in range(2000):
    dt = datetime(1990, 1, 1, tzinfo=UTC) + timedelta(days=rnd.randrange(0, 365 * 20))
    check(dt.weekday() == VirtualClock(dt).display_now.weekday(), f"weekday distinto en {dt}")

# --- orden de eventos --------------------------------------------------------
c = VirtualClock(REAL_START)
log: list[str] = []
times = [rnd.randrange(1, 10_000_000) for _ in range(500)]
for i, secs in enumerate(times):
    c.schedule_in(timedelta(seconds=secs), (lambda i=i: log.append(f"e{i}")), f"e{i}")
# un evento programado desde dentro de otro, y otro cancelado
c.schedule_in(timedelta(seconds=5),
              lambda: c.schedule_in(timedelta(seconds=1), lambda: log.append("nested")), "outer")
ev = c.schedule_in(timedelta(seconds=3), lambda: log.append("cancelled"), "cancel-me")
c.cancel(ev)

ran = c.advance(timedelta(seconds=max(times) + 1))
check(ran == 502, f"eventos ejecutados {ran}")
items = [(times[i], i, f"e{i}") for i in range(500)] + [(6, 10**6, "nested")]   # nested: t=6, programado el último
expected = [name for _, _, name in sorted(items)]
check(log == expected, "eventos fuera de orden (o el anidado no corrió en su instante)")
check("cancelled" not in log, "el evento cancelado corrió")

# cada callback ve la hora exacta de su evento
c = VirtualClock(REAL_START)
seen: list[datetime] = []
for secs in (10, 20, 30):
    c.schedule_in(timedelta(seconds=secs), lambda: seen.append(c.real_now()))
c.advance(timedelta(seconds=25))
check(seen == [REAL_START + timedelta(seconds=10), REAL_START + timedelta(seconds=20)], "hora en callback")
check(c.real_now() == REAL_START + timedelta(seconds=25), "reloj tras avance parcial")
try:
    c.advance_to(REAL_START)
    check(False, "el reloj retrocedió")
except ValueError:
    pass

# --- tiempo real acelerado ----------------------------------------------------
w = World(REAL_START)
hits: list[datetime] = []
for day in range(1, 31):
    w.clock.schedule_in(timedelta(days=day), (lambda: hits.append(w.clock.real_now())), "daily")
w.run(speed=86400 * 60)          # 60 días virtuales por segundo real
t0 = time.time()
while len(hits) < 30 and time.time() - t0 < 10:
    time.sleep(0.05)
w.stop()
check(len(hits) == 30, f"a 60 días/s solo corrieron {len(hits)} eventos diarios en 10 s")
check(hits == sorted(hits), "eventos en tiempo real fuera de orden")
check(w.clock.real_now() >= REAL_START + timedelta(days=30), "el reloj no avanzó 30 días")
w.speed = 0
before = w.clock.real_now()
w.run(0)
time.sleep(0.3)
check(w.clock.real_now() == before, "en pausa el reloj avanzó")
w.stop()

print("CLOCK OK")
