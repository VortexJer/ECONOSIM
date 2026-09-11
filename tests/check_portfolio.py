"""G6: revalorización al avanzar el reloj, equity = efectivo + posiciones, y P&L exacto."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.ledger import to_cents

w, md, mask, a = make_market_world(seed="pf", years=5, initial_eur=100000.0)
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = a.mask.aliases[0]
real = a.mask.to_real(alias)
s = md.series[real]

with LiveApp(a.app()) as trade:
    T = trade.url
    # comprar 100 al arranque
    qty = 100.0
    buy = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty, "side": "buy", "type": "market"}).json()
    fill_mask = float(buy["filled_avg_price"])
    fill_real = mask.unindex_price(real, fill_mask)
    cash_after_buy = w.balance()

    # equity justo tras comprar ≈ efectivo + valor de mercado (a precio medio, sin cruzar spread)
    acc = requests.get(T("/v2/account"), headers=H).json()
    px_now = a._masked_price(alias)
    mv_real = qty * mask.unindex_price(real, px_now)
    check(abs(float(acc["equity"]) - (cash_after_buy/100 + mv_real)) < 0.02, f"equity tras compra: {acc['equity']}")

    # avanzar 30 sesiones y comprobar que la posición se revaloriza a los precios REALES
    start_day = a._session_asof()
    future = [b for b in s.bars if b.day > start_day][:30]
    target = future[-1]
    # avanzar el reloj hasta el cierre de ese día
    from datetime import datetime
    from econosim.market.calendar import MARKET_CLOSE, UTC
    w.advance_to(datetime.combine(target.day, MARKET_CLOSE, UTC) - timedelta(minutes=1))

    pos = requests.get(T(f"/v2/positions/{alias}"), headers=H).json()
    px_target_mask = mask.index_price(real, target.close)
    check(abs(float(pos["current_price"]) - round(px_target_mask, 4)) < 1e-2, f"precio actual {pos['current_price']} != {px_target_mask}")
    # market value = qty * precio actual enmascarado; unrealized P&L = mv - coste
    mv_mask = qty * px_target_mask
    cost_mask = qty * fill_mask
    check(abs(float(pos["market_value"]) - mv_mask) < 0.05, "market_value")
    check(abs(float(pos["unrealized_pl"]) - (mv_mask - cost_mask)) < 0.05, "unrealized_pl")
    # el signo del P&L coincide con el retorno real del subyacente
    real_ret = target.close / fill_real - 1
    check((float(pos["unrealized_pl"]) > 0) == (real_ret > 0), "signo del P&L no sigue al retorno real")

    # equity de la cuenta cuadra: efectivo + valor de mercado real
    acc = requests.get(T("/v2/account"), headers=H).json()
    mv_real_now = qty * target.close
    check(abs(float(acc["equity"]) - (w.balance()/100 + mv_real_now)) < 0.05, f"equity {acc['equity']} no cuadra")
    check(abs(a.equity_cents() - (w.balance() + to_cents(mv_real_now))) <= 2, "equity_cents no cuadra")

    # vender todo: el P&L realizado = (precio venta real - coste real) * qty
    cash_before_sell = w.balance()
    sell = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty, "side": "sell", "type": "market"}).json()
    sell_real = mask.unindex_price(real, float(sell["filled_avg_price"]))
    realized = to_cents(qty * sell_real)
    check(w.balance() - cash_before_sell == realized, "efectivo de la venta total")
    check(requests.get(T(f"/v2/positions/{alias}"), headers=H).status_code == 404, "posición debería cerrarse")
    # el dinero final refleja el retorno real menos el spread pagado dos veces
    net = w.balance() - 100000 * 100
    gross_ret = qty * (sell_real - fill_real)
    check(abs(net - to_cents(gross_ret)) <= 2, f"P&L neto {net} != {to_cents(gross_ret)}")
    check((net > 0) == (sell_real > fill_real), "signo del P&L realizado")

print("PORTFOLIO OK")
