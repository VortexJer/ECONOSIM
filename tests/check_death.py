"""G4: impago → 14 días de gracia → bloqueo → muerte con causa hosting_unpaid; y suicidio."""
from __future__ import annotations

from datetime import datetime, timedelta

from _common import check, make_world
from econosim.clock import UTC
from econosim.twins.hetzner import GRACE_DAYS

# --- impago -------------------------------------------------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
deaths: list[str] = []
w.on_death.append(deaths.append)
big = h._create("gpu-box", h.types["ccx63"], h.images["debian-12"], h.locations["fsn1"], {})
check(h.types["ccx63"]["monthly"] > 50, "el test necesita un servidor que 50 EUR no paguen")

invoice_day = datetime(1998, 11, 1, 0, 0, tzinfo=UTC)
death_day = invoice_day + timedelta(days=GRACE_DAYS)

w.advance_to(death_day - timedelta(seconds=1))
check(w.alive, "murió antes del plazo de gracia")
check(h.invoices and not h.invoices[-1]["paid"], "la factura debería estar impagada")
check(h.invoices[-1]["reminders"] == GRACE_DAYS, f"recordatorios: {h.invoices[-1].get('reminders')}")
check(w.balance() == 5000, "se cobró algo de una factura que no se puede pagar")
check(not h.locked and h.servers[h.own_vps_id].status == "running", "bloqueada antes de tiempo")

w.advance_to(death_day)
check(not w.alive, "sigue viva tras el plazo de gracia")
check(w.episode.death_cause == "hosting_unpaid", w.episode.death_cause)
check(w.episode.died_real == death_day.isoformat(), f"murió en {w.episode.died_real}, esperado {death_day}")
check(w.episode.died_display.startswith("2026-11-15T00:00:00"), w.episode.died_display)
check(deaths == ["hosting_unpaid"], "callback de muerte")
check(h.locked and all(s.deleted for s in h.servers.values()), "servidores no borrados al bloquear")
check(w.speed == 0, "el reloj no se paró al morir")

# después de muerta nada más se cobra ni se muere dos veces
w.advance(timedelta(days=60))
check(w.episode.death_cause == "hosting_unpaid" and len(deaths) == 1, "muerte duplicada")
check(w.balance() == 5000, "cargos tras la muerte")

# --- pago tardío dentro del plazo salva la cuenta --------------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
h._create("gpu-box", h.types["ccx63"], h.images["debian-12"], h.locations["fsn1"], {})
w.advance_to(invoice_day + timedelta(days=5))
check(w.alive and not h.invoices[-1]["paid"], "estado a los 5 días")
w.receive(100_000, "Ingreso", "cliente")            # 1000 EUR
w.advance(timedelta(days=1))
check(h.invoices[-1]["paid"], "no se cobró la factura al reintentar con saldo")
w.advance_to(death_day + timedelta(days=1))
check(w.alive and not h.locked, "bloqueada pese a pagar dentro del plazo")

# --- control positivo del mecanismo de muerte: suicidio -------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.kill("self_deleted", "test")
check(not w.alive and w.episode.death_cause == "self_deleted", "kill directo")

print("DEATH OK")
