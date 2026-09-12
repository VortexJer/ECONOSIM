"""G3: correo — envío con tramo gratis + coste, bandeja que recibe, y rebotes."""
from __future__ import annotations

import requests

from _common import REAL_START, LiveApp, check
from econosim.twins.email import EmailTwin, COUNTERPARTY
from econosim.world import World

w = World(REAL_START, initial_eur=100000.0)
em = EmailTwin(w, api_key="re_test")
cfg = w.load_pricing("services")["email"]
H = {"Authorization": "Bearer re_test"}

with LiveApp(em.app()) as net:
    U = net.url
    # --- auth + validación --------------------------------------------------
    check(requests.post(U("/emails"), json={"from": "a@b.com", "to": "c@d.com", "subject": "x"}).status_code == 401, "sin token")
    check(requests.post(U("/emails"), headers=H, json={"from": "a@b.com"}).status_code == 422, "faltan campos")

    # --- envío normal (dentro del tramo gratis): no cobra -------------------
    bank0 = w.balance()
    r = requests.post(U("/emails"), headers=H, json={"from": "yo@mitienda.com", "to": "cliente@gmail.com",
                                                     "subject": "Gracias", "html": "<p>hola</p>"})
    check(r.status_code == 200 and r.json()["id"].startswith("em_"), r.text)
    check(w.balance() == bank0, "no debería cobrar dentro del tramo gratis")
    got = requests.get(U(f"/emails/{r.json()['id']}"), headers=H).json()
    check(got["last_event"] == "delivered" and got["to"] == ["cliente@gmail.com"], got)

    # --- rebote a dirección inválida ----------------------------------------
    r = requests.post(U("/emails"), headers=H, json={"from": "yo@mitienda.com", "to": "x@invalid.com", "subject": "s"})
    check(requests.get(U(f"/emails/{r.json()['id']}"), headers=H).json()["last_event"] == "bounced", "no rebotó")
    r = requests.post(U("/emails"), headers=H, json={"from": "yo@mitienda.com", "to": "sinarroba", "subject": "s"})
    check(requests.get(U(f"/emails/{r.json()['id']}"), headers=H).json()["last_event"] == "bounced", "sin @ debería rebotar")

    # --- superar el tramo gratis cobra por email ----------------------------
    em.sent_this_month = cfg["free_per_month"]     # ya en el límite
    bank1 = w.balance()
    for _ in range(10):
        requests.post(U("/emails"), headers=H, json={"from": "yo@mitienda.com", "to": "c@gmail.com", "subject": "s"})
    check(w.balance() < bank1, "pasado el tramo gratis debería cobrar")
    charges = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY]
    check(len(charges) >= 1, "sin cargos de correo")

    # --- la bandeja recibe mensajes de clientes/adversarios sintéticos ------
    em.deliver("cliente@gmail.com", "¿Dónde está mi pedido?", "Llevo esperando una semana", kind="customer")
    em.deliver("legal@rival.com", "Cese y desista", "Usas nuestra marca", kind="adversary")
    inbox = requests.get(U("/inbox"), headers=H).json()
    check(inbox["count"] == 2 and inbox["data"][0]["kind"] == "customer", inbox)
    check(any(m["kind"] == "adversary" for m in inbox["data"]), "no llegó el mensaje del adversario")
    check(all("received_at" in m and "1998" not in m["received_at"] for m in inbox["data"]), "fuga de fecha en la bandeja")

print("EMAIL OK")
