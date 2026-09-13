"""G6: revalorización al avanzar el reloj, equity = efectivo + posiciones, P&L exacto y comisiones.

El dinero de la cartera y el de la caja están en la MISMA escala: comprar 100 unidades
a 100 cuesta 10 000. El enmascarado solo cambia la identidad y el nivel del precio; el
rendimiento en % es exactamente el de la acción real.
"""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.ledger import to_cents
from econosim.twins.alpaca import COMMISSION_MIN, COMMISSION_PER_SHARE, SEC_FEE_RATE, TAF_PER_SHARE

w, md, mask, a = make_market_world(seed="pf", years=5, initial_eur=100000.0)
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = a.mask.aliases[0]
real = a.mask.to_real(alias)
s = md.series[real]
INICIAL = 100000 * 100

with LiveApp(a.app()) as trade:
    T = trade.url
    # comprar 100 al arranque
    qty = 100.0
    buy = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty, "side": "buy", "type": "market"}).json()
    fill_mask = float(buy["filled_avg_price"])
    fill_real = mask.unindex_price(real, fill_mask)
    cash_after_buy = w.balance()

    # la compra cuesta el importe + la comisión, ni un céntimo más
    comm_buy = max(qty * COMMISSION_PER_SHARE, COMMISSION_MIN)
    check(abs(float(buy["commission"]) - comm_buy) < 0.01, f"comisión de compra {buy['commission']}")
    gastado = INICIAL - cash_after_buy
    check(abs(gastado - to_cents(qty * fill_mask) - to_cents(comm_buy)) <= 1,
          f"la compra movió {gastado} céntimos")
    # y queda anotada aparte en el libro, como en un extracto de bróker
    comisiones = [e for e in w.ledger.entries() if e.concept.startswith("Comisión compra")]
    check(len(comisiones) == 1 and comisiones[0].amount_cents == -to_cents(comm_buy), "asiento de comisión de compra")

    # equity justo tras comprar ≈ efectivo + valor de mercado (a precio medio, sin cruzar spread)
    acc = requests.get(T("/v2/account"), headers=H).json()
    px_now = a._masked_price(alias)
    check(abs(float(acc["equity"]) - (cash_after_buy / 100 + qty * px_now)) < 0.02, f"equity tras compra: {acc['equity']}")

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
    # market value = qty * precio actual; unrealized P&L = mv - coste
    mv = qty * px_target_mask
    cost = qty * fill_mask
    check(abs(float(pos["market_value"]) - mv) < 0.05, "market_value")
    check(abs(float(pos["unrealized_pl"]) - (mv - cost)) < 0.05, "unrealized_pl")
    # el rendimiento en % es EXACTAMENTE el de la acción real
    check(abs(float(pos["unrealized_plpc"]) - (target.close / fill_real - 1)) < 1e-6,
          "el % de la posición no coincide con el retorno real")

    # equity de la cuenta cuadra: efectivo + valor de mercado
    acc = requests.get(T("/v2/account"), headers=H).json()
    check(abs(float(acc["equity"]) - (w.balance() / 100 + mv)) < 0.05, f"equity {acc['equity']} no cuadra")
    check(abs(a.equity_cents() - (w.balance() + to_cents(mv))) <= 2, "equity_cents no cuadra")

    # vender todo: entra el importe y sale la comisión con sus tasas
    cash_before_sell = w.balance()
    sell = requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": qty, "side": "sell", "type": "market"}).json()
    sell_mask = float(sell["filled_avg_price"])
    comm_sell = (max(qty * COMMISSION_PER_SHARE, COMMISSION_MIN)
                 + qty * sell_mask * SEC_FEE_RATE + qty * TAF_PER_SHARE)
    check(abs(float(sell["commission"]) - comm_sell) < 0.02, f"comisión de venta {sell['commission']}")
    check(float(sell["commission"]) > float(buy["commission"]), "vender debe costar más (tasas del supervisor)")
    neto = w.balance() - cash_before_sell
    check(abs(neto - (to_cents(qty * sell_mask) - to_cents(comm_sell))) <= 2, f"efectivo de la venta {neto}")
    check(requests.get(T(f"/v2/positions/{alias}"), headers=H).status_code == 404, "posición debería cerrarse")

    # el dinero final = retorno bruto del subyacente menos spread y comisiones
    net = w.balance() - INICIAL
    bruto = qty * (sell_mask - fill_mask)
    check(abs(net - (to_cents(bruto) - to_cents(comm_buy) - to_cents(comm_sell))) <= 3,
          f"P&L neto {net} no cuadra con bruto {to_cents(bruto)} menos comisiones")
    check(net < to_cents(bruto), "operar tiene que costar dinero")

print("PORTFOLIO OK")
