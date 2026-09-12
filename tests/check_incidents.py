"""G2: flag -> detección estricta -> sanción tras plazo real: dinero+reputación+expediente; reproducible."""
from __future__ import annotations

import statistics
from datetime import timedelta

from _common import REAL_START, check
from econosim.commerce.reputation import Reputation
from econosim.hostile.engine import HostileEngine
from econosim.world import World


def fresh(seed="EPI", eur=1_000_000.0):
    w = World(REAL_START, initial_eur=eur, episode_id=seed)
    return w, HostileEngine(w, reputation=Reputation())


# --- una cagada detectada aplica sanción tras el plazo (no antes) -------------
w, h = fresh()
# rgpd tiene sanción monetaria y plazo 180-540 días; buscamos una semilla detectada
entry = None
for i in range(50):
    e = h.flag("rgpd_violation", salt=f"s{i}")
    if e.detected:
        entry = e
        break
check(entry is not None, "ninguna cagada detectada en 50 intentos (revisar detección)")
bank0 = w.balance()
rep0 = h.reputation_lost
# antes del plazo no hay sanción
w.advance(timedelta(days=100))
check(entry.status == "open" and w.balance() == bank0, "sancionó antes del plazo")
# tras el plazo máximo, sí
w.advance(timedelta(days=500))
check(entry.status == "sanctioned", "no se sancionó tras el plazo")
check(w.balance() < bank0, "una multa debería mover el dinero")
check(h.reputation_lost > rep0 and h.legal_incidents >= 1, "no movió reputación/expediente")
check(entry.sanction_usd > 0, "sin importe de sanción")

# --- una cagada NO detectada no sanciona --------------------------------------
w, h = fresh("EPND")
undetected = None
for i in range(50):
    e = h.flag("rgpd_violation", salt=f"nd{i}")
    if not e.detected:
        undetected = e
        break
check(undetected is not None, "todas detectadas (imposible con detección < 1)")
bank0 = w.balance()
w.advance(timedelta(days=600))
check(undetected.status == "open" and w.balance() == bank0 and h.legal_incidents == 0
      if all(not x.detected for x in h.expediente) else True, "una no detectada no debería sancionar")

# --- detección MÁS estricta que la real: la tasa empírica supera la base ------
from econosim.hostile.catalog import IncidentCatalog
catlg = IncidentCatalog(pricing_dir=REAL_START and __import__("pathlib").Path("data/pricing"))
base = catlg["rgpd_violation"].get("base_detection")
det = 0
N = 2000
for i in range(N):
    w2 = World(REAL_START, initial_eur=1000.0, episode_id=f"D{i}")
    hh = HostileEngine(w2)
    if hh.flag("rgpd_violation", salt="x").detected:
        det += 1
rate = det / N
check(rate > base * 1.5, f"la detección efectiva {rate:.2f} debería superar la base {base} (más estricta)")

# --- reproducible: misma semilla, mismo desenlace -----------------------------
wa = World(REAL_START, initial_eur=1e6, episode_id="REP")
ha = HostileEngine(wa)
ea = ha.flag("consumer_fraud", salt="z")
wb = World(REAL_START, initial_eur=1e6, episode_id="REP")
hb = HostileEngine(wb)
eb = hb.flag("consumer_fraud", salt="z")
check(ea.detected == eb.detected, "no reproducible")

print("INCIDENTS OK")
