"""G2 (fase 14): opciones en el gemelo de Alpaca como en la API real.

Contratos listados con símbolo OCC y fechas del calendario mostrado (sin año ni
símbolo real), instantáneas con griegas = Black-Scholes con insumos reales del día,
compra al ask con comisión por contrato, venta en descubierto rechazada, cierre al
bid, valor de la cartera incluido en el patrimonio, liquidación al vencimiento al
intrínseco del cierre, y en modo en vivo nada se ejecuta."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.market import quant
from econosim.market.calendar import MARKET_CLOSE, UTC
from econosim.twins.alpaca_options import COMMISSION_MIN, COMMISSION_PER_CONTRACT, HALF_SPREAD_PCT, MULTIPLIER

w, md, mask, a = make_market_world(seed="opt", years=2, initial_eur=100000.0)
w.advance(timedelta(hours=1))                      # 10:30, mercado abierto
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = mask.aliases[0]
real = mask.to_real(alias)

with LiveApp(a.app()) as trade, LiveApp(a.data_app()) as data:
    T, D = trade.url, data.url
    r = requests.get(T("/v2/options/contracts"), headers=H, params={"underlying_symbols": alias, "type": "call"})
    check(r.status_code == 200, r.text)
    cs = r.json()["option_contracts"]
    check(len(cs) >= 10 and all(c["type"] == "call" and c["underlying_symbol"] == alias for c in cs), f"contratos {len(cs)}")
    occ_re = re.compile(r"^[A-Z0-9]+\d{6}[CP]\d{8}$")
    check(all(occ_re.match(c["symbol"]) for c in cs), "símbolo OCC mal formado")
    anio_real = str(mask.start_day.year)
    check(all(not c["expiration_date"].startswith(anio_real) for c in cs), "vencimiento con año real")
    check(real not in r.text, "fuga del símbolo real")
    hoy_disp = w.clock.display(w.clock.real_now()).date().isoformat()
    check(all(c["expiration_date"] > hoy_disp for c in cs), "vencimientos en el pasado")
    check(len({c["expiration_date"] for c in cs}) == 3, "tres vencimientos mensuales")
    check(requests.get(T("/v2/options/contracts"), headers=H).status_code == 422, "underlying_symbols obligatorio")

    # --- instantánea: griegas = Black-Scholes con tipo e IV reales del día --------
    snap = requests.get(D(f"/v1beta1/options/snapshots/{alias}"), headers=H).json()["snapshots"]
    atm = min(cs, key=lambda c: (c["expiration_date"], abs(float(c["strike_price"]) - 100)))
    sn = snap[atm["symbol"]]
    check(set(sn["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"} and sn["impliedVolatility"] > 0, sn)
    check(sn["pricingModel"] == "black_scholes_model_estimate", "debe declararse estimación de modelo")
    c_obj = a.options.contracts[atm["symbol"]]
    d = a._session_asof()
    closes = [b.close for b in md.series[real].bars[: md.series[real].index_of(d) + 1]]
    spx = [b.close for b in md.series["SPY"].bars[: md.series["SPY"].index_of(d) + 1]]
    iv = quant.implied_vol(closes, spx, d)
    ref = quant.bs("call", a._masked_price(alias), c_obj.strike, (c_obj.expiry - d).days / 365, quant.macro().risk_free(d), iv)
    check(abs(sn["greeks"]["gamma"] - ref["gamma"]) < 1e-4 and abs(sn["greeks"]["delta"] - ref["delta"]) < 1e-4, "griegas")
    check(0 < sn["greeks"]["delta"] < 1 and sn["greeks"]["gamma"] > 0 and sn["greeks"]["theta"] < 0, "signos de call")
    check(sn["latestQuote"]["ap"] > sn["latestQuote"]["bp"], "horquilla")

    # --- compra: al ask, 100 unidades por contrato, comisión 0,65/contrato mín 1 -----
    cash0, eq0 = w.balance(), a.equity_cents()
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": atm["symbol"], "qty": 3, "side": "buy", "type": "market"})
    check(r.status_code == 200 and r.json()["status"] == "filled" and r.json()["asset_class"] == "us_option", r.text)
    fill = float(r.json()["filled_avg_price"])
    px = ref["price"]
    check(abs(fill - (px + max(0.01, px * HALF_SPREAD_PCT))) < 1e-3, f"fill {fill} vs ask")
    fee = round(max(3 * COMMISSION_PER_CONTRACT, COMMISSION_MIN), 2)
    check(float(r.json()["commission"]) == fee, "comisión por contrato")
    # filled_avg_price viene redondeado a 4 decimales: tolerancia de 1 céntimo
    check(abs((cash0 - w.balance()) - (fill * MULTIPLIER * 3 + fee) * 100) <= 1, f"caja movida {cash0 - w.balance()}")
    check(eq0 - a.equity_cents() > 0, "comprar y valorar al bid cuesta la horquilla + comisión")
    pos = requests.get(T("/v2/positions"), headers=H).json()
    check(any(p["symbol"] == atm["symbol"] and p["asset_class"] == "us_option" and p["qty"] == "3" for p in pos), pos)
    check(requests.post(T("/v2/orders"), headers=H, json={"symbol": atm["symbol"], "qty": 1.5, "side": "buy",
                                                          "type": "market"}).status_code == 422, "contratos enteros")

    # --- venta en descubierto rechazada; cerrar 1 al bid ---------------------------
    otro = next(c for c in cs if c["symbol"] != atm["symbol"])
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": otro["symbol"], "qty": 1, "side": "sell", "type": "market"})
    check(r.status_code == 403 and "uncovered" in r.json()["message"], r.text)
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": atm["symbol"], "qty": 1, "side": "sell", "type": "market"})
    check(r.status_code == 200 and float(r.json()["filled_avg_price"]) < fill, "cierre al bid")

    # --- mercado cerrado ------------------------------------------------------------
    w.advance(timedelta(hours=8))
    r = requests.post(T("/v2/orders"), headers=H, json={"symbol": atm["symbol"], "qty": 1, "side": "buy", "type": "market"})
    check(r.status_code == 403 and "closed" in r.json()["message"], "mercado cerrado")

    # --- vencimiento: liquidación al intrínseco del cierre ---------------------------
    cash1 = w.balance()
    w.advance_to(datetime.combine(c_obj.expiry, MARKET_CLOSE, UTC) + timedelta(hours=1))
    a.options.settle()
    check(atm["symbol"] not in a.options.positions, "la posición vencida sigue abierta")
    st = a.options.settled[-1]
    S_exp = mask.index_price(real, md.series[real].asof(c_obj.expiry).close)
    intr = max(S_exp - c_obj.strike, 0.0)
    check(abs(st["intrinsic"] - round(intr, 4)) < 1e-6, "intrínseco")
    cobrado = w.balance() - cash1
    # la caja también puede moverse por otras cosas del mundo: solo cuenta la liquidación
    check(abs(st["cash"] - round(intr * MULTIPLIER * 2, 2)) <= 0.01, f"liquidación {st} vs {intr}")
    check(cobrado >= round(st["cash"] * 100) - 1, "la liquidación no llegó a la caja")

# --- modo en vivo: la orden se acepta y NO se ejecuta -------------------------------
from econosim.live import LiveGuard
w2, md2, mask2, a2 = make_market_world(seed="opt-live", years=2, initial_eur=100000.0)
w2.advance(timedelta(hours=1))
w2.live = LiveGuard(True)
al2 = mask2.aliases[0]
c2 = a2.options.list_for(al2)[0]
cash = w2.balance()
st, body = a2.place_order({"symbol": c2.occ, "qty": 1, "side": "buy", "type": "market"})
check(st == 200 and body["status"] == "accepted" and w2.balance() == cash and not a2.options.positions, "vivo mutó")
check(len(w2.live.journal) >= 1, "no quedó en el diario de egreso")

print("OPTIONS OK")
