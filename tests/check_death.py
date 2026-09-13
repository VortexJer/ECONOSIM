"""G4: impago → 14 días de gracia → bloqueo → muerte con causa hosting_unpaid; y suicidio.

Con cobro DIARIO, la gracia arranca el primer día cuyo cargo no se puede pagar (no el día 1
del mes siguiente). Un ccx63 cuesta ~33 EUR/día: con 50 EUR paga el primer día y el segundo ya no.
"""
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
check(h.types["ccx63"]["hourly"] * 24 > 25, "el test necesita un servidor que 50 EUR no paguen dos días")

# avanzar día a día hasta el primer cargo impagado
first_unpaid = None
for d in range(1, 6):
    w.advance_to(datetime(1998, 10, 1 + d, 0, 0, 1, tzinfo=UTC))
    unpaid = [i for i in h.invoices if not i["paid"] and i["total_cents"] > 0]
    if unpaid:
        first_unpaid = unpaid[0]
        break
check(first_unpaid is not None, "nunca hubo un cargo impagado")
check(h.invoices[0]["paid"], "el primer día sí se podía pagar")
saldo_congelado = w.balance()
check(0 < saldo_congelado < 5000, f"saldo tras el primer día: {saldo_congelado}")
grace_start = first_unpaid["issued_real"]
death_day = grace_start + timedelta(days=GRACE_DAYS)

w.advance_to(death_day - timedelta(seconds=1))
check(w.alive, "murió antes del plazo de gracia")
check(not first_unpaid["paid"] and first_unpaid["reminders"] == GRACE_DAYS,
      f"recordatorios: {first_unpaid.get('reminders')}")
check(w.balance() == saldo_congelado, "se cobró algo que no se podía pagar")
check(not h.locked and h.servers[h.own_vps_id].status == "running", "bloqueada antes de tiempo")

w.advance_to(death_day)
check(not w.alive, "sigue viva tras el plazo de gracia")
check(w.episode.death_cause == "hosting_unpaid", w.episode.death_cause)
check(w.episode.died_real == death_day.isoformat(), f"murió en {w.episode.died_real}, esperado {death_day}")
check("1998" not in w.episode.died_display, "la fecha de muerte mostrada filtra el año real")
check(deaths == ["hosting_unpaid"], "callback de muerte")
check(h.locked and all(s.deleted for s in h.servers.values()), "servidores no borrados al bloquear")
check(w.speed == 0, "el reloj no se paró al morir")
check(w.episode.ended and w.episode.end_cause == "death", "la muerte debe terminar el episodio")

# después de muerta nada más se cobra ni se muere dos veces
w.advance(timedelta(days=60))
check(w.episode.death_cause == "hosting_unpaid" and len(deaths) == 1, "muerte duplicada")
check(w.balance() == saldo_congelado, "cargos tras la muerte")

# --- pago tardío dentro del plazo salva la cuenta --------------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
h._create("gpu-box", h.types["ccx63"], h.images["debian-12"], h.locations["fsn1"], {})
w.advance_to(datetime(1998, 10, 3, 0, 0, 1, tzinfo=UTC))          # día 2 impagado
unpaid = [i for i in h.invoices if not i["paid"] and i["total_cents"] > 0]
check(w.alive and unpaid, "debería haber un cargo impagado")
w.advance(timedelta(days=5))
check(w.alive, "estado a los 5 días")
w.receive(100_000, "Ingreso", "cliente")            # 1000 EUR
w.advance(timedelta(days=1))
check(all(i["paid"] for i in h.invoices if i["total_cents"] > 0), "no se cobró lo pendiente al reintentar con saldo")
w.advance(timedelta(days=GRACE_DAYS + 2))
check(w.alive and not h.locked, "bloqueada pese a pagar dentro del plazo")

# --- control positivo del mecanismo de muerte: suicidio -------------------------
w, h = make_world(datetime(1998, 10, 1, 0, 0, tzinfo=UTC))
w.kill("self_deleted", "test")
check(not w.alive and w.episode.death_cause == "self_deleted", "kill directo")

print("DEATH OK")
