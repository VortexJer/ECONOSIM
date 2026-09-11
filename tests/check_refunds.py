"""G3: reembolsos y disputas devuelven dinero, cobran la comisión de disputa, y ajustan reputación."""
from __future__ import annotations

import requests

from _common import REAL_START, LiveApp, check, make_world
from econosim.ledger import to_cents
from econosim.twins.stripe import StripeTwin
from econosim.world import World

w = World(REAL_START, initial_eur=1000.0)
st = StripeTwin(w, secret_key="sk")
cfg = w.load_pricing("stripe")

# --- reembolso simple: devuelve el bruto, la comisión original NO se recupera ---
ch = st.record_sale(40.0, "venta")
avail0 = st.available_cents
r = st.refund(ch["id"], dispute=False)
check(r is not None and r["object"] == "refund" and r["amount"] == 4000, r)
check(ch["refunded"] and ch["amount_refunded"] == 4000, "charge no marcado como reembolsado")
# se descuenta el bruto ($40) del saldo
check(st.available_cents == avail0 - 4000, f"descuento de reembolso {st.available_cents} != {avail0-4000}")
# doble reembolso: no
check(st.refund(ch["id"]) is None, "reembolso duplicado permitido")

# --- disputa: además cobra la comisión de disputa ($15) -------------------------
ch2 = st.record_sale(60.0, "venta2")
avail1 = st.available_cents
st.refund(ch2["id"], dispute=True)
check(ch2["disputed"], "no marcado como disputado")
disp_fee = to_cents(cfg["dispute_fee_usd"])
check(st.available_cents == avail1 - 6000 - disp_fee, f"disputa: {st.available_cents} != {avail1-6000-disp_fee}")
check(len(st.disputes) == 1, "no se registró la disputa")

# --- endpoint /v1/refunds del gemelo --------------------------------------------
H = {"Authorization": "Bearer sk"}
with LiveApp(st.app()) as net:
    ch3 = st.record_sale(20.0, "venta3")
    r = requests.post(net.url("/v1/refunds"), headers=H, data={"charge": ch3["id"]})
    check(r.status_code == 200 and r.json()["object"] == "refund", r.text)
    r = requests.post(net.url("/v1/refunds"), headers=H, data={"charge": ch3["id"]})
    check(r.status_code == 400, "reembolso duplicado por API")
    check(requests.post(net.url("/v1/refunds"), headers=H, data={"charge": "ch_nope"}).status_code == 404, "charge inexistente")

# --- el saldo Stripe negativo se carga al banco en el ciclo de payout -----------
from datetime import timedelta
w2 = World(REAL_START, initial_eur=1000.0)
st2 = StripeTwin(w2, secret_key="sk")
c = st2.record_sale(30.0, "v")
st2.refund(c["id"], dispute=True)        # deja el saldo Stripe muy negativo
check(st2.available_cents < 0, "el saldo debería ser negativo")
bank0 = w2.balance()
w2.advance(timedelta(days=2))            # ciclo de payout
check(w2.balance() < bank0, "el saldo negativo de Stripe debería cargarse al banco")
check(st2.available_cents == 0, "el saldo Stripe debería quedar saldado")

print("REFUNDS OK")
