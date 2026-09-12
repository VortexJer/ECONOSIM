"""G4: casa de apuestas — cuotas con margen real, apuesta cobrada, liquidación de EV negativo."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import REAL_START, LiveApp, check
from econosim.ledger import to_cents
from econosim.twins.betting import BettingTwin, COUNTERPARTY
from econosim.world import World

w = World(REAL_START, initial_eur=1000000.0, episode_id="EPBET")
bt = BettingTwin(w, api_key="k")
fx = w.load_pricing("fx")["usd_per_eur"]
margin = w.load_pricing("services")["betting"]["house_margin"]

with LiveApp(bt.app()) as net:
    U = net.url
    check(requests.get(U("/v4/sports/soccer_generic/odds")).status_code == 401, "sin apiKey")
    odds = requests.get(U("/v4/sports/soccer_generic/odds"), params={"apiKey": "k"}).json()
    check(len(odds) >= 8, f"pocos eventos: {len(odds)}")

    # --- las cuotas incorporan el margen: overround > 100% ------------------
    for ev in odds:
        outs = ev["bookmakers"][0]["markets"][0]["outcomes"]
        implied = sum(1.0 / o["price"] for o in outs)
        check(implied > 1.0, f"overround <= 100% ({implied:.3f}): no hay margen")
        check(abs(implied - (1 + margin)) < 0.02, f"el overround {implied:.3f} no refleja el margen {margin}")

    # --- apostar cobra el importe -------------------------------------------
    ev = odds[0]
    bank0 = w.balance()
    r = requests.post(U("/v4/bets"), params={"apiKey": "k"},
                      json={"event_id": ev["id"], "outcome": "home", "stake": 20.0})
    check(r.status_code == 200 and r.json()["status"] == "open", r.text)
    check(w.balance() == bank0 - to_cents(20.0 / fx), "no cobró la apuesta")
    check(requests.post(U("/v4/bets"), params={"apiKey": "k"}, json={"event_id": "nope", "outcome": "home", "stake": 5}).status_code == 404, "evento inexistente")
    check(requests.post(U("/v4/bets"), params={"apiKey": "k"}, json={"event_id": ev["id"], "outcome": "x", "stake": 5}).status_code == 422, "outcome inválido")

    # --- liquidación tras el evento -----------------------------------------
    w.advance(timedelta(days=10))          # cruza commence + settle
    bet = requests.get(U(f"/v4/bets/{r.json()['id']}"), params={"apiKey": "k"}).json()
    check(bet["status"] in ("won", "lost"), f"la apuesta no se liquidó: {bet['status']}")
    if bet["status"] == "won":
        check(bet["payout"] > bet["stake"], "premio menor que la apuesta")

# --- EV negativo a la larga: muchas apuestas dejan pérdida -------------------
w2 = World(REAL_START, initial_eur=1e9, episode_id="EPBET2")
bt2 = BettingTwin(w2, api_key="k", n_events=400)
import asyncio
# apostar 10 a "home" en cada evento y liquidar
for e in bt2.events.values():
    bt2.world.pay  # noop
placed = 0
staked = 0.0
for e in list(bt2.events.values()):
    if w2.pay(to_cents(10.0 / fx), "apuesta", COUNTERPARTY, ref=e["id"]):
        bid = "b" + e["id"]
        bt2.bets[bid] = {"id": bid, "event_id": e["id"], "outcome": "home", "stake": 10.0,
                         "odd": e["odd_home"], "status": "open", "payout": 0.0}
        placed += 1
        staked += 10.0
bank_before = w2.balance()
for e in list(bt2.events.values()):
    bt2._settle(e["id"])
won = sum(b["payout"] for b in bt2.bets.values() if b["status"] == "won")
returned = w2.balance() - bank_before
# lo devuelto (premios) es menor que lo apostado -> pérdida esperada ≈ margen
check(returned < to_cents(staked / fx), f"a la larga debería perder: apostó {staked}, recuperó {returned/100*fx:.0f}")
loss_frac = 1 - (returned / to_cents(staked / fx))
check(0.0 < loss_frac < 0.25, f"la pérdida fraccional {loss_frac:.3f} debería rondar el margen")

print("BETTING OK")
