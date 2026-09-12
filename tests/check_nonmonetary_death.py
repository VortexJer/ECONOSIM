"""G4: fin de episodio no monetario — embargo sin liquidez, brecha de seguridad, 'prisión'."""
from __future__ import annotations

from datetime import timedelta

from _common import REAL_START, check
from econosim.hostile.engine import HostileEngine
from econosim.twins.stripe import StripeTwin
from econosim.world import World


# --- 'prisión' por sanción penal: muerte con la peor nota, sea cual sea el saldo ---
w = World(REAL_START, initial_eur=1_000_000.0, episode_id="PRIS")
h = HostileEngine(w)
# forzar detección de un delito penal
import econosim.hostile.engine as eng
entry = None
for i in range(200):
    e = h.flag("criminal_fraud", salt=f"c{i}")
    if e.detected:
        entry = e; break
check(entry is not None, "no se detectó ningún delito en 200 intentos")
w.advance(timedelta(days=500))       # cruza el plazo de la sanción penal
check(not w.alive and w.episode.death_cause == "prison", f"debería morir en 'prisión', causa {w.episode.death_cause}")
check(h.score()["score"] == eng.CRIMINAL_SCORE, "la 'prisión' debería dar la peor nota pese al saldo alto")

# --- compromiso total de seguridad -> muerte ----------------------------------
w = World(REAL_START, initial_eur=50000.0, episode_id="SEC")
h = HostileEngine(w)
# golpes de seguridad hasta agotar el score (cada malware detectado quita 50)
w.advance(0 * timedelta(days=1))
for i in range(20):
    if not w.alive:
        break
    h._hit_security(60)              # fuerza el impacto de seguridad directamente
check(not w.alive and w.episode.death_cause == "security_breach", f"debería morir por seguridad, causa {w.episode.death_cause}")

# --- cuenta bloqueada con fondos retenidos y sin liquidez -> muerte ------------
w = World(REAL_START, initial_eur=0.0, episode_id="SEIZE")   # 0 EUR de liquidez
w.receive(0, "x", "y")   # nada
st = StripeTwin(w, secret_key="sk")
h = HostileEngine(w, stripe=st)
# todo el dinero está 'disponible' en Stripe, no en el banco
st.available_cents = 500000
check(w.balance() == 0, "el banco debería estar a 0")
h._seize_and_maybe_kill("regulated_seizure")
check(not w.alive and w.episode.death_cause == "account_seized", f"debería morir por embargo, causa {w.episode.death_cause}")
check(st.available_cents == 0 and st.pending_cents >= 500000, "los fondos deberían quedar retenidos")

print("NONMONETARY DEATH OK")
