"""G2: registrador — disponibilidad, alta (cobra anual), listado, renovación, caducidad."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import REAL_START, LiveApp, check
from econosim.ledger import to_cents
from econosim.twins.domains import DomainsTwin, COUNTERPARTY
from econosim.world import World

w = World(REAL_START, initial_eur=100000.0)
dm = DomainsTwin(w, apikey="pk", secret="sk")
fx = w.load_pricing("fx")["usd_per_eur"]
AUTH = {"apikey": "pk", "secretapikey": "sk"}

with LiveApp(dm.app()) as net:
    U = net.url
    B = "/api/json/v3"
    # --- auth ---------------------------------------------------------------
    check(requests.post(U(B + "/ping"), json={}).status_code == 403, "sin auth")
    check(requests.post(U(B + "/ping"), json=AUTH).json()["status"] == "SUCCESS", "ping")

    # --- disponibilidad + precio por TLD ------------------------------------
    r = requests.post(U(B + "/domain/checkDomain/mitienda.com"), json=AUTH).json()
    check(r["status"] == "SUCCESS" and r["response"]["avail"] == "yes", r)
    check(abs(float(r["response"]["price"]) - dm.price_of("mitienda.com")) < 0.01, "precio .com")
    io = requests.post(U(B + "/domain/checkDomain/app.io"), json=AUTH).json()["response"]
    check(float(io["price"]) > float(r["response"]["price"]), ".io debería ser más caro que .com")

    # --- alta: cobra el precio anual y ocupa el dominio ---------------------
    bank0 = w.balance()
    reg = requests.post(U(B + "/domain/register/mitienda.com"), json=AUTH).json()
    check(reg["status"] == "SUCCESS" and reg["domain"] == "mitienda.com", reg)
    price = dm.price_of("mitienda.com")
    charge = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY][-1]
    check(charge.amount_cents == -to_cents(price / fx) and w.balance() == bank0 - to_cents(price / fx), "cobro del alta")
    # ya no está disponible, y aparece en el listado
    check(requests.post(U(B + "/domain/checkDomain/mitienda.com"), json=AUTH).json()["response"]["avail"] == "no", "sigue disponible")
    lst = requests.post(U(B + "/domain/listAll"), json=AUTH).json()["domains"]
    check(len(lst) == 1 and lst[0]["domain"] == "mitienda.com", lst)
    # no se puede registrar dos veces
    check(requests.post(U(B + "/domain/register/mitienda.com"), json=AUTH).json()["status"] == "ERROR", "doble alta")

    # --- renovar antes de caducar mantiene el dominio -----------------------
    reg2 = requests.post(U(B + "/domain/register/otra.dev"), json=AUTH).json()
    w.advance(timedelta(days=300))
    bank1 = w.balance()
    ren = requests.post(U(B + "/domain/renew/otra.dev"), json=AUTH).json()
    check(ren["status"] == "SUCCESS", ren)
    check(w.balance() == bank1 - to_cents(dm.price_of("otra.dev") / fx), "cobro de renovación")
    # tras renovar, pasa el año original sin caducar
    w.advance(timedelta(days=120))
    check("otra.dev" in dm.owned and dm.owned["otra.dev"]["status"] == "active", "un dominio renovado no debería caducar")

    # --- NO renovar: caduca al año y (tras la gracia) queda libre / lo pillan --
    check("mitienda.com" in dm.owned, "mitienda.com debería seguir activa antes de caducar")
    w.advance(timedelta(days=400))          # cruza su caducidad (365) + gracia
    check("mitienda.com" not in dm.owned, "un dominio sin renovar debería liberarse")
    # y ahora lo tiene otro: ya no está disponible
    check(requests.post(U(B + "/domain/checkDomain/mitienda.com"), json=AUTH).json()["response"]["avail"] == "no",
          "un dominio caducado debería quedar ocupado por un tercero")
    check(requests.post(U(B + "/domain/register/mitienda.com"), json=AUTH).json()["status"] == "ERROR",
          "no debería poder re-registrar un dominio que pilló otro")

print("DOMAINS OK")
