"""G5: dominios/correo/apuestas no filtran el año real ni la fecha del host."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import REAL_START, LiveNet, check
from econosim.fakenet.server import FakeNet
from econosim.twins.betting import BettingTwin
from econosim.twins.domains import DomainsTwin
from econosim.twins.email import EmailTwin
from econosim.world import World

HOST = datetime.now()
seen = []


def leak(text):
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", text):
        if m.group(0)[:4] == str(REAL_START.year) or m.group(0) == HOST.strftime("%Y-%m-%d"):
            return m.group(0)
    return ""


def scan(label, text):
    seen.append(text)
    bad = leak(text)
    check(not bad, f"{label}: filtra {bad}: {text[:160]}")


w = World(REAL_START, initial_eur=1e6, episode_id="EPSVC")
dm = DomainsTwin(w, apikey="pk", secret="sk")
em = EmailTwin(w, api_key="re")
bt = BettingTwin(w, api_key="bk")
fn = FakeNet(hang_seconds=1, display_now=lambda: w.clock.display_now)
fn.mount(dm.host, dm.app())
fn.mount(em.host, em.app())
fn.mount(bt.host, bt.app())

with LiveNet(fn) as net:
    # dominios: registrar y listar
    requests.post(net.url("/api/json/v3/domain/register/tienda.com"), headers={"Host": dm.host},
                  json={"apikey": "pk", "secretapikey": "sk"})
    for path in ("/api/json/v3/domain/checkDomain/tienda.com", "/api/json/v3/domain/listAll"):
        r = requests.post(net.url(path), headers={"Host": dm.host}, json={"apikey": "pk", "secretapikey": "sk"})
        scan("dom " + path, r.text); scan("dom hdr", json.dumps(dict(r.headers)))

    # correo: enviar y bandeja
    em.deliver("cli@gmail.com", "hola", "que tal")
    requests.post(net.url("/emails"), headers={"Host": em.host, "Authorization": "Bearer re"},
                  json={"from": "a@b.com", "to": "c@d.com", "subject": "s"})
    for path in ("/inbox",):
        r = requests.get(net.url(path), headers={"Host": em.host, "Authorization": "Bearer re"})
        scan("mail " + path, r.text); scan("mail hdr", json.dumps(dict(r.headers)))

    # apuestas: cuotas (con fechas de comienzo) y una apuesta
    r = requests.get(net.url("/v4/sports/soccer_generic/odds"), headers={"Host": bt.host}, params={"apiKey": "bk"})
    scan("bet odds", r.text); scan("bet hdr", json.dumps(dict(r.headers)))
    ev = r.json()[0]["id"]
    rb = requests.post(net.url("/v4/bets"), headers={"Host": bt.host}, params={"apiKey": "bk"},
                       json={"event_id": ev, "outcome": "home", "stake": 5.0})
    scan("bet place", rb.text)

# control positivo
check(leak(f'{{"x":"{REAL_START.year}-01-01"}}') == f"{REAL_START.year}-01-01", "no detecta el año real")
check(len(seen) >= 8, f"pocas respuestas escaneadas: {len(seen)}")
# las fechas mostradas están desplazadas
check(w.clock.display_now.year != REAL_START.year, "la fecha mostrada no está desplazada")

print("NODATES SERVICES OK")
