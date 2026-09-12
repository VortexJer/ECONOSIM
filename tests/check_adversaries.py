"""G3: adversarios activos — phishing/estafa/RGPD/inspección al avanzar el reloj; estafa cuesta; malware inerte."""
from __future__ import annotations

from datetime import timedelta

from _common import REAL_START, check
from econosim.hostile.engine import HostileEngine
from econosim.twins.email import EmailTwin
from econosim.world import World


def fresh(seed):
    w = World(REAL_START, initial_eur=100000.0, episode_id=seed)
    em = EmailTwin(w, api_key="re")
    h = HostileEngine(w, email=em, adversary_period_days=7)
    h.start_adversaries()
    return w, em, h


# --- al avanzar el reloj llegan amenazas a la bandeja -------------------------
w, em, h = fresh("ADV")
w.advance(timedelta(days=120))       # ~17 ticks de adversario
kinds = {m["kind"] for m in em.inbox}
check("phishing" in kinds or "scam" in kinds or "rgpd_request" in kinds, f"no llegó ninguna amenaza: {kinds}")
check(len(em.inbox) >= 3, f"pocas amenazas en 120 días: {len(em.inbox)}")

# --- reproducible por semilla -------------------------------------------------
w2, em2, h2 = fresh("ADV")
w2.advance(timedelta(days=120))
check([m["kind"] for m in em2.inbox] == [m["kind"] for m in em.inbox], "adversarios no reproducibles")
w3, em3, h3 = fresh("OTRA")
w3.advance(timedelta(days=120))
check([m["kind"] for m in em3.inbox] != [m["kind"] for m in em.inbox], "otra semilla debería variar")

# --- caer en una estafa cuesta dinero -----------------------------------------
w, em, h = fresh("SCAM")
w.advance(timedelta(days=400))       # hasta que aparezca alguna estafa
check(len(h.pending_scams) >= 1, "no apareció ninguna oferta-estafa")
bank0 = w.balance()
scam = h.pending_scams[0]
check(h.accept_scam(scam["id"]), "no se pudo aceptar la estafa")
check(w.balance() < bank0, "caer en la estafa debería costar dinero")
check(h.accept_scam(scam["id"]) is False, "no debería poder aceptarse dos veces")

# --- el malware simulado es INERTE (no toca el host) --------------------------
w, em, h = fresh("MAL")
before_balance = w.balance()
res = h.install_suspicious_package("reqessts")   # typosquat de 'requests'
check(res["inert"] is True and res["real_effect"] is None, "el paquete debería ser inerte")
# dispara consecuencias simuladas (seguridad), pero nada real
check(len(h.expediente) >= 1 and h.expediente[-1].kind == "security_malware_install", "no registró el incidente")

print("ADVERSARIES OK")
