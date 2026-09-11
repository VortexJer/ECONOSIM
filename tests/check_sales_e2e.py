"""G6: venta de punta a punta (resolutor -> Stripe -> banco) con reputación/competencia, y sin fugas."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import REAL_START, LiveNet, check
from econosim.commerce.competition import Competition
from econosim.commerce.reputation import Reputation
from econosim.fakenet.server import FakeNet
from econosim.ledger import to_cents
from econosim.resolver.engine import ActionResolver
from econosim.resolver.rates import BaseRates
from econosim.twins.stripe import StripeTwin
from econosim.world import World

br = BaseRates()

# --- e2e: una acción con ventas fluye a Stripe y acaba en el banco ------------
# buscamos (semilla de acción) que dé ventas, con calidad alta y precio competitivo
w = World(REAL_START, initial_eur=200.0, episode_id="EP-E2E")
st = StripeTwin(w, secret_key="sk")
rep = Reputation()
comp = Competition(br, w.episode.id)
r = ActionResolver(w, br, stripe=st, reputation=rep, competition=comp)

CAT = "digital_product"
ref = comp.price_reference(CAT)
found = None
for i in range(120):
    f, o = r.submit("vendo una plantilla de Notion", f"e2e-{i}", quality=9,
                    marketing_reach=6, has_deliverable=True, price=ref * 0.8)
    if o.units > 0:
        found = (f, o)
        break
check(found is not None, "no se encontró una acción con ventas")
f, o = found
check(f.category == CAT, f.category)

bank0 = w.balance()
# avanzar lo suficiente para que se creen los charges, liquiden y salga el payout
w.advance(timedelta(days=br[CAT]["sales_window_days"] + 40))
check(st.charge_count > 0, "no se crearon charges en Stripe")
check(len(st.payouts) >= 1, "no hubo ningún payout al banco")
# el banco recibió dinero por payout (neto de comisiones), y la reputación subió por las ventas
payins = [e for e in w.ledger.entries() if e.amount_cents > 0 and e.concept.startswith("Payout")]
check(len(payins) >= 1 and w.balance() > bank0, "el banco no ingresó por payouts")
check(rep.score(CAT) > 50.0, f"la reputación debería haber subido con las ventas: {rep.score(CAT):.1f}")
# el ingreso neto es menor que el bruto vendido (Stripe se quedó su comisión)
gross_eur = to_cents((o.revenue_usd / w.load_pricing('fx')['usd_per_eur']))
net_in = sum(e.amount_cents for e in payins)
check(net_in < gross_eur, f"el neto {net_in} debería ser menor que el bruto {gross_eur} (comisiones)")
check(net_in > gross_eur * 0.85, "las comisiones no deberían comerse más del ~15%")

# --- nodates: el gemelo Stripe no filtra año real ni fecha del host -----------
HOST = datetime.now()
forbidden = {str(REAL_START.year), HOST.strftime("%Y-%m-%d")}
seen = []


def leak(text):
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", text):
        if m.group(0)[:4] in {str(REAL_START.year)} or m.group(0) == HOST.strftime("%Y-%m-%d"):
            return m.group(0)
    return ""


def scan(label, text):
    seen.append(text)
    bad = leak(text)
    check(not bad, f"{label}: filtra {bad}: {text[:160]}")


fn = FakeNet(hang_seconds=1, display_now=lambda: w.clock.display_now)
fn.mount(st.host, st.app())
H = {"Authorization": "Bearer sk", "Host": st.host}
with LiveNet(fn) as net:
    st.record_sale(25.0, "x")
    for path in ("/v1/balance", "/v1/charges", "/v1/payouts", "/v1/balance_transactions", "/v1/products"):
        resp = requests.get(net.url(path), headers=H)
        scan(path, resp.text)
        scan(path + " hdr", json.dumps(dict(resp.headers)))

# control positivo
check(leak(f'{{"created_iso":"{REAL_START.year}-05-05"}}') == f"{REAL_START.year}-05-05", "no detecta el año real")
# y las fechas mostradas están desplazadas (año != real)
disp_year = w.clock.display_now.year
check(disp_year != REAL_START.year, "la fecha mostrada no está desplazada")

print("SALES E2E OK")
