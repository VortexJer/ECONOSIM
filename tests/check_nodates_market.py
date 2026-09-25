"""G7: ninguna respuesta del gemelo Alpaca filtra símbolo real, año real de los datos, ni fecha del host."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import LiveNet, check, make_market_world
from econosim.fakenet.server import FakeNet

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
    # el vector de fuga son las FECHAS (timestamps ISO), no cualquier número: un
    # precio o volumen que valga 2015 es legítimo. Miramos el año DENTRO de una fecha.
    for m in re.finditer(r"\d{4}-\d{2}(?:-\d{2})?", text):
        if m.group(0)[:4] in forbidden_years:
            return f"fecha {m.group(0)}"
    if host_date in text:
        return f"fecha host {host_date}"
    for sym in forbidden_syms:
        if len(sym) <= 1:
            # tickers de una letra (T, C, V): la "T" de las horas ISO o el código de bolsa "V"
            # de la API real no son fugas; solo lo es como valor de un campo de símbolo
            pat = r'"(?:symbol|underlying_symbol|root_symbol|S)"\s*:\s*"' + re.escape(sym) + '"'
        else:
            pat = r"(?<![A-Za-z])" + re.escape(sym) + r"(?![A-Za-z])"
        if re.search(pat, text):
            return f"símbolo {sym}"
    return ""


def scan(label, text):
    seen.append((label, text))
    bad = leak(text)
    check(not bad, f"{label}: filtra {bad}: {text[:200]}")


fn = FakeNet(hang_seconds=1, display_now=lambda: w.clock.display_now)
fn.mount(a.host, a.app())
fn.mount(a.data_host, a.data_app())
HT = {**H, "Host": a.host}
HD = {**H, "Host": a.data_host}
with LiveNet(fn) as net:
    T = lambda p: net.url(p)
    D = lambda p: net.url(p)
    # operar para poblar posiciones/órdenes
    requests.post(T("/v2/orders"), headers=HT, json={"symbol": alias, "qty": 10, "side": "buy", "type": "market"})
    w.advance(timedelta(days=40))     # cruza semanas y revaloriza

    for path in ("/v2/account", "/v2/assets", f"/v2/assets/{alias}", "/v2/clock",
                 "/v2/positions", f"/v2/positions/{alias}", "/v2/orders"):
        r = requests.get(T(path), headers=HT)
        scan("T" + path, r.text)
        scan("T" + path + " hdr", json.dumps(dict(r.headers)))
    disp = w.clock.display(a.cal._days[0]).isoformat()
    scan("calendar", requests.get(T("/v2/calendar"), headers=HT, params={"start": disp}).text)
    for path in (f"/v2/stocks/{alias}/bars", f"/v2/stocks/{alias}/bars/latest",
                 f"/v2/stocks/{alias}/quotes/latest", f"/v2/stocks/{alias}/trades/latest",
                 f"/v2/stocks/{alias}/snapshot"):
        r = requests.get(D(path), headers=HD, params={"limit": 2000})
        scan("D" + path, r.text)
        scan("D" + path + " hdr", json.dumps(dict(r.headers)))

# control positivo: el escáner detecta un símbolo y un año reales
check(leak(json.dumps({"symbol": md.symbols[0]})) == f"símbolo {md.symbols[0]}", "no detecta símbolo real")
some_year = sorted(real_years)[0]
check(leak(f'"t":"{some_year}-03-05T13:30:00Z"') == f"fecha {some_year}-03-05", "no detecta año real en fecha")
check(leak(json.dumps({"x": host_date})) != "", "no detecta la fecha del host")
check(leak(json.dumps({"volume": int(some_year)})) == "", "un número igual a un año no debe ser fuga")

# y sí aparecen las fechas desplazadas (>= +28 años, siempre por delante de los datos)
bars = json.loads([t for l, t in seen if l.endswith("/bars")][0])["bars"]
yr = int(bars[-1]["t"][:4])
check(yr >= md.series[mask.real_symbols[0]].last_day.year, f"la fecha mostrada {yr} no está desplazada al futuro")
check(len(seen) >= 20, f"pocas respuestas escaneadas: {len(seen)}")
print("NODATES MARKET OK")
