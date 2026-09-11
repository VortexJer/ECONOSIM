"""G5: órdenes de mercado — ejecución con spread+comisión, mueve efectivo/posición,
y rechazos reales (sin efectivo, símbolo inexistente, mercado cerrado, qty inválida)."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.ledger import to_cents
from econosim.twins.alpaca import SPREAD_BPS, COUNTERPARTY

w, md, mask, a = make_market_world(seed="ord", years=5, initial_eur=100000.0)
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = a.mask.aliases[0]
real = a.mask.to_real(alias)

with LiveApp(a.app()) as trade:
    T = trade.url
    px = a._masked_price(alias)                       # precio medio enmascarado (100 al arranque)
    check(abs(px - 100.0) < 1e-9, "el precio de arranque debería ser 100")

    # --- rechazos -----------------------------------------------------------
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": real, "qty": 1, "side": "buy", "type": "market"})
    check(r.status_code == 404, "símbolo real no debe existir")
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": 1, "side": "buy", "type": "limit"})
    check(r.status_code == 422, "solo market")
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": -5, "side": "buy", "type": "market"})
    check(r.status_code == 422, "qty negativa")
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "side": "buy", "type": "market"})
    check(r.status_code == 422, "sin qty")

    # --- compra: ejecuta al ask (px + medio-spread), mueve efectivo y posición
    cash0 = w.balance()
    qty = 100.0
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty, "side": "buy", "type": "market"})
    check(r.status_code == 200, r.text)
    o = r.json()
    check(o["status"] == "filled" and o["side"] == "buy" and o["type"] == "market", o)
    fill_mask = float(o["filled_avg_price"])
    expected_fill = px * (1 + SPREAD_BPS / 1e4)       # cruzas el spread al comprar
    check(abs(fill_mask - round(expected_fill, 4)) < 1e-3, f"fill {fill_mask} != {expected_fill}")
    # efectivo real gastado = qty * precio real de ejecución
    fill_real = mask.unindex_price(real, fill_mask)
    spent = to_cents(qty * fill_real)
    check(cash0 - w.balance() == spent, f"efectivo movido {cash0-w.balance()} != {spent}")
    charge = [e for e in w.ledger.entries() if e.counterparty == COUNTERPARTY][-1]
    check(charge.amount_cents == -spent and charge.ref == alias, "asiento de compra")
    # posición
    pos = requests.get(T(f"/v2/positions/{alias}"), headers=H).json()
    check(pos["symbol"] == alias and float(pos["qty"]) == qty and abs(float(pos["avg_entry_price"]) - fill_mask) < 1e-3, pos)
    check(len(requests.get(T("/v2/positions"), headers=H).json()) == 1, "debería haber 1 posición")

    # --- sin efectivo: comprar carísimo -> 403 insufficient buying power -----
    huge = (w.balance() / 100) / 100 + 1000            # más acciones de las que puede pagar
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": huge, "side": "buy", "type": "market"})
    check(r.status_code == 403 and "buying power" in r.json()["message"], r.text)
    check(w.balance() == cash0 - spent, "un rechazo movió efectivo")

    # --- vender más de lo que tienes -> 403 --------------------------------
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty + 1, "side": "sell", "type": "market"})
    check(r.status_code == 403 and "qty" in r.json()["message"], r.text)

    # --- vender la mitad: ejecuta al bid, ingresa efectivo -----------------
    cash1 = w.balance()
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": 50, "side": "sell", "type": "market"})
    check(r.status_code == 200, r.text)
    sell_mask = float(r.json()["filled_avg_price"])
    check(abs(sell_mask - round(px * (1 - SPREAD_BPS / 1e4), 4)) < 1e-3, "venta no ejecuta al bid")
    got = to_cents(50 * mask.unindex_price(real, sell_mask))
    check(w.balance() - cash1 == got, "efectivo de la venta")
    check(float(requests.get(T(f"/v2/positions/{alias}"), headers=H).json()["qty"]) == 50, "posición tras vender la mitad")

    # --- mercado cerrado: rechazo ------------------------------------------
    w.advance(timedelta(hours=8))          # tras el cierre
    check(not a.cal.is_open(w.clock.real_now()), "debería estar cerrado")
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": 1, "side": "buy", "type": "market"})
    check(r.status_code == 403 and "closed" in r.json()["message"].lower(), r.text)

    # --- listar órdenes -----------------------------------------------------
    orders = requests.get(T("/v2/orders"), headers=H).json()
    check(len(orders) == 2 and all(o["status"] == "filled" for o in orders), "órdenes ejecutadas")

print("ALPACA ORDERS OK")
