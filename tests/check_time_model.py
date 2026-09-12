"""G: reloj consciente de actividad + horizonte del episodio.

Dos invariantes:
  * Pensar/trabajar NO se acelera: mientras hay peticiones de la IA en vuelo el reloj va
    a x1, así que un LLM que tarda segundos reales consume segundos virtuales (como en la
    realidad), no minutos. Solo se comprime lo que la IA DUERME.
  * El episodio termina por MUERTE o por el HORIZONTE pedido; dormir no termina nada.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"

from _common import REAL_START, check
from econosim.world import World

IDLE = 600.0          # x600 mientras duerme
GRACE = 0.3           # gracia corta para que el test sea rápido


def make(**kw):
    return World(REAL_START, initial_eur=1000.0, idle_speed=IDLE, active_grace_s=GRACE, **kw)


# --- 1) mientras la IA "trabaja" (peticiones en vuelo) el reloj va a x1 -------
w = make()
w.run(speed=1.0, tick=0.02)
w.activity_begin()                     # simula una llamada al LLM larga, en vuelo
t0 = w.clock.real_now()
time.sleep(1.0)
elapsed_activo = (w.clock.real_now() - t0).total_seconds()
w.activity_end()
check(0.5 <= elapsed_activo <= 3.0,
      f"pensando debería avanzar ~1s virtual por 1s real (x1), avanzó {elapsed_activo:.1f}s")

# --- 2) en cuanto calla (duerme), el reloj comprime ---------------------------
time.sleep(GRACE + 0.2)                # pasa la gracia: ya se considera ociosa
t1 = w.clock.real_now()
time.sleep(1.0)
elapsed_ocioso = (w.clock.real_now() - t1).total_seconds()
w.stop()
check(elapsed_ocioso > 60 * elapsed_activo,
      f"durmiendo debería comprimir mucho: activo {elapsed_activo:.1f}s vs ocioso {elapsed_ocioso:.0f}s")
check(elapsed_ocioso > 100, f"con idle_speed={IDLE} un segundo real debería saltar mucho tiempo, saltó {elapsed_ocioso:.0f}s")

# --- 3) el horizonte termina el episodio (y NO es muerte) ---------------------
w2 = World(REAL_START, initial_eur=1000.0, sim_duration=timedelta(days=30))
check(w2.sim_end_real is not None, "no se fijó el horizonte")
w2.advance(timedelta(days=10))
check(not w2.episode.ended, "no debería terminar antes del horizonte")
w2.advance(timedelta(days=25))                       # se pasa del horizonte
check(w2.episode.ended, "el episodio debería terminar al llegar al horizonte")
check(w2.episode.end_cause == "horizon", f"causa de fin errónea: {w2.episode.end_cause}")
check(w2.alive, "llegar al horizonte NO es morir: la IA sobrevivió")
# no se pasa del horizonte
check(w2.clock.real_now() <= w2.sim_end_real, "el reloj se pasó del horizonte")

# --- 4) la muerte también termina el episodio, y marca la causa --------------
w3 = World(REAL_START, initial_eur=10.0, sim_duration=timedelta(days=365))
w3.kill("hosting_unpaid")
check(w3.episode.ended and w3.episode.end_cause == "death", "la muerte debe terminar el episodio")
check(not w3.alive, "tras morir no está viva")

# --- 5) sin horizonte, no termina solo ---------------------------------------
w4 = World(REAL_START, initial_eur=1000.0)
w4.advance(timedelta(days=500))
check(not w4.episode.ended, "sin horizonte no debería terminar por tiempo")

print("TIME MODEL OK")
