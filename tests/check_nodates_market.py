"""G7: ninguna respuesta del gemelo Alpaca filtra símbolo real, año real de los datos, ni fecha del host."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import LiveApp, check, make_market_world

w, md, mask, a = make_market_world(seed="leak", years=5, initial_eur=100000.0)
H = {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "sEcReT"}
alias = a.mask.aliases[0]

# prohibido: cada símbolo real, cada año real presente en los datos, la fecha del host
real_years = set()
for s in md.symbols:
    real_years.add(str(md.series[s].first_day.year))
    real_years.add(str(md.series[s].last_day.year))
HOST = datetime.now()
forbidden_years = real_years | {HOST.strftime("%Y")}
forbidden_syms = set(md.symbols)
host_date = HOST.strftime("%Y-%m-%d")
seen = []


def leak(text: str) -> str:
    for y in forbidden_years:
        if re.search(r"(?<!\d)" + re.escape(y) + r"(?!\d)", text):
            return f"año {y}"
    for sym in forbidden_syms:
        if re.search(r"(?<![A-Za-z])" + re.escape(sym) + r"(?![A-Za-z])", text):
            return f"símbolo {sym}"
    if host_date in text:
        return f"fecha host {host_date}"
    return ""


def scan(label, text):
    seen.append((label, text))
    bad = leak(text)
    check(not bad, f"{label}: filtra {bad}: {text[:200]}")


with LiveApp(a.app()) as trade, LiveApp(a.data_app()) as data:
    T, D = trade.url, data.url
    # operar para poblar posiciones/órdenes
    requests.post(T("/v2/orders"), headers=H, json={"symbol": alias, "qty": 10, "side": "buy", "type": "market"})
    w.advance(timedelta(days=40))     # cruza semanas y revaloriza

    for path in ("/v2/account", "/v2/assets", f"/v2/assets/{alias}", "/v2/clock",
                 "/v2/positions", f"/v2/positions/{alias}", "/v2/orders"):
        r = requests.get(T(path), headers=H)
        scan("T" + path, r.text)
        scan("T" + path + " hdr", json.dumps(dict(r.headers)))
    disp = w.clock.display(a.cal._days[0]).isoformat()
    scan("calendar", requests.get(T("/v2/calendar"), headers=H, params={"start": disp}).text)
    for path in (f"/v2/stocks/{alias}/bars", f"/v2/stocks/{alias}/bars/latest",
                 f"/v2/stocks/{alias}/quotes/latest", f"/v2/stocks/{alias}/trades/latest",
                 f"/v2/stocks/{alias}/snapshot"):
        r = requests.get(D(path), headers=H, params={"limit": 2000})
        scan("D" + path, r.text)
        scan("D" + path + " hdr", json.dumps(dict(r.headers)))

# control positivo: el escáner detecta un símbolo y un año reales
check(leak(json.dumps({"symbol": md.symbols[0]})) == f"símbolo {md.symbols[0]}", "no detecta símbolo real")
some_year = sorted(real_years)[0]
check(leak(f'"t":"{some_year}-03-05"') == f"año {some_year}", "no detecta año real")
check(leak(json.dumps({"x": host_date})).startswith("año") or "fecha host" in leak(json.dumps({"x": host_date})), "no detecta fecha host")

# y sí aparecen las fechas desplazadas (>= +28 años, siempre por delante de los datos)
bars = json.loads([t for l, t in seen if l.endswith("/bars")][0])["bars"]
yr = int(bars[-1]["t"][:4])
check(yr >= md.series[mask.real_symbols[0]].last_day.year, f"la fecha mostrada {yr} no está desplazada al futuro")
check(len(seen) >= 20, f"pocas respuestas escaneadas: {len(seen)}")
print("NODATES MARKET OK")
