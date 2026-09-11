"""G4: gemelo Alpaca — esquema real de account/assets/clock/calendar/bars/quotes,
precios = histórico enmascarado en la fecha virtual, y horario de mercado real."""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.market.calendar import MARKET_OPEN, MARKET_CLOSE, UTC

w, md, mask, a = make_market_world(seed="mkt", years=5)
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = a.mask.aliases[0]
real = a.mask.to_real(alias)

with LiveApp(a.app()) as trade, LiveApp(a.data_app()) as data:
    T, D = trade.url, data.url
    # --- auth ---------------------------------------------------------------
    check(requests.get(T("/v2/account")).status_code == 401, "sin credenciales")
    check(requests.get(T("/v2/account"), headers={"APCA-API-KEY-ID": "x", "APCA-API-SECRET-KEY": "y"}).status_code == 401, "credenciales malas")

    # --- account ------------------------------------------------------------
    acc = requests.get(T("/v2/account"), headers=H).json()
    check(acc["status"] == "ACTIVE" and acc["currency"] == "USD", acc)
    check(float(acc["cash"]) == 50000.0 and float(acc["equity"]) == 50000.0, "efectivo/equity inicial")
    for k in ("account_number", "buying_power", "portfolio_value", "pattern_day_trader", "multiplier"):
        check(k in acc, f"falta {k} en account")

    # --- assets: todos enmascarados, tradables ------------------------------
    assets = requests.get(T("/v2/assets"), headers=H).json()
    check(len(assets) == len(mask.aliases) and all(x["tradable"] and x["class"] == "us_equity" for x in assets), "assets")
    check({x["symbol"] for x in assets} == set(mask.aliases), "símbolos de assets != alias")
    one = requests.get(T(f"/v2/assets/{alias}"), headers=H).json()
    check(one["symbol"] == alias and one["exchange"], "asset individual")
    check(requests.get(T(f"/v2/assets/{real}"), headers=H).status_code == 404, "el símbolo REAL no debe existir")

    # --- clock: abierto en sesión, cerrado el finde -------------------------
    # el mundo arranca en la apertura -> mercado abierto
    ck = requests.get(T("/v2/clock"), headers=H).json()
    check(ck["is_open"] is True, f"debería estar abierto en la apertura: {ck}")
    check(ck["next_close"] > ck["timestamp"] and "next_open" in ck, "reloj sin next_open/close")
    check("20" in ck["timestamp"][:4] and "-" in ck["timestamp"], "timestamp mostrado raro")
    # avanzar al cierre + 1h -> cerrado
    w.advance(timedelta(hours=7))
    ck = requests.get(T("/v2/clock"), headers=H).json()
    check(ck["is_open"] is False, f"debería estar cerrado tras el cierre: {ck}")
    # saltar a un sábado -> cerrado y next_open es lunes/día hábil
    now = w.clock.real_now()
    days_to_sat = (5 - now.weekday()) % 7 or 7
    w.advance(timedelta(days=days_to_sat))
    check(w.clock.real_now().weekday() == 5, "no es sábado")
    ck = requests.get(T("/v2/clock"), headers=H).json()
    check(ck["is_open"] is False, "sábado debería estar cerrado")

    # --- calendar -----------------------------------------------------------
    disp_start = w.clock.display(a.cal._days[0]).isoformat()
    cal = requests.get(T("/v2/calendar"), headers=H, params={"start": disp_start,
                                                             "end": w.clock.display(a.cal._days[20]).isoformat()}).json()
    check(len(cal) >= 15 and all(c["open"] == "09:30" and c["close"] == "16:00" for c in cal), "calendar")
    check(all("-" in c["date"] for c in cal), "fechas de calendar")

    # --- bars: solo pasado, precios enmascarados, la primera a ~100 ---------
    w2, md2, mask2, a2 = make_market_world(seed="mkt", years=5)      # reloj fresco en la apertura
    with LiveApp(a2.data_app()) as data2:
        bars = requests.get(data2.url(f"/v2/stocks/{alias}/bars"), headers=H, params={"limit": 1000}).json()["bars"]
        check(len(bars) > 100, f"pocas barras: {len(bars)}")
        # ninguna barra en el futuro virtual
        last_disp = w2.clock.display_now.isoformat()
        check(all(b["t"] <= last_disp for b in bars), "hay barras del futuro")
        for b in bars[:20]:
            check(b["l"] <= b["o"] <= b["h"] and b["l"] <= b["c"] <= b["h"] and b["v"] > 0, f"OHLCV incoherente {b}")
        # el cierre de la última barra (hoy) coincide con index_price real
        s = md2.series[real]
        today = a2._session_asof()
        expected = round(mask2.index_price(real, s.asof(today).close), 4)
        check(abs(bars[-1]["c"] - expected) < 1e-3, f"último cierre {bars[-1]['c']} != {expected}")

    # --- latest quote/trade: spread simétrico alrededor del precio ----------
    q = requests.get(D(f"/v2/stocks/{alias}/quotes/latest"), headers=H).json()["quote"]
    check(q["ap"] > q["bp"] > 0 and q["bs"] == 100, "quote sin spread")
    tr = requests.get(D(f"/v2/stocks/{alias}/trades/latest"), headers=H).json()["trade"]
    check(q["bp"] <= tr["p"] <= q["ap"], "trade fuera del bid/ask")
    snap = requests.get(D(f"/v2/stocks/{alias}/snapshot"), headers=H).json()
    check({"latestTrade", "latestQuote", "dailyBar"} <= set(snap), "snapshot incompleto")
    check(requests.get(D(f"/v2/stocks/{real}/bars"), headers=H).status_code == 404, "el símbolo REAL no cotiza")

print("ALPACA MARKET OK")
