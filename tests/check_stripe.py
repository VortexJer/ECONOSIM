"""G1: gemelo Stripe — esquema real (products, prices, checkout, charges, balance, payouts), auth, tabla con fuente."""
from __future__ import annotations

import json

import requests

from _common import ROOT, LiveApp, check, make_world
from econosim.twins.stripe import StripeTwin

cfg = json.loads((ROOT / "data" / "pricing" / "stripe.json").read_text(encoding="utf-8"))
src = cfg["_source"]
check(src["fees_url"].startswith("http") and src["payout_url"].startswith("http") and src["retrieved"], "stripe.json sin fuente")
check(cfg["fee_percent"] == 0.029 and cfg["fee_fixed_usd"] == 0.30 and cfg["dispute_fee_usd"] == 15.0, "comisiones")

w, h = make_world(initial_eur=1000.0)
st = StripeTwin(w, secret_key="sk_test_123")
H = {"Authorization": "Bearer sk_test_123"}

with LiveApp(st.app()) as net:
    U = net.url
    # --- auth ---------------------------------------------------------------
    check(requests.get(U("/v1/balance")).status_code == 401, "sin clave")
    check(requests.get(U("/v1/balance"), headers={"Authorization": "Bearer sk_bad"}).status_code == 401, "clave mala")

    # --- product + price (form-encoded, como el SDK real) -------------------
    r = requests.post(U("/v1/products"), headers=H, data={"name": "Plantilla Notion"})
    check(r.status_code == 200 and r.json()["object"] == "product" and r.json()["id"].startswith("prod_"), r.text)
    prod = r.json()["id"]
    check(requests.post(U("/v1/products"), headers=H, data={}).status_code == 400, "producto sin nombre")
    r = requests.post(U("/v1/prices"), headers=H, data={"product": prod, "unit_amount": "1200", "currency": "usd"})
    check(r.status_code == 200 and r.json()["unit_amount"] == 1200 and r.json()["product"] == prod, r.text)
    check(requests.post(U("/v1/prices"), headers=H, data={"product": "prod_nope", "unit_amount": "1"}).status_code == 400, "precio de producto inexistente")

    # --- checkout session ---------------------------------------------------
    r = requests.post(U("/v1/checkout/sessions"), headers=H, data={"mode": "payment", "success_url": "https://x/ok"})
    check(r.status_code == 200 and r.json()["object"] == "checkout.session" and r.json()["url"].startswith("https://checkout.stripe.com"), r.text)
    sid = r.json()["id"]
    check(requests.get(U(f"/v1/checkout/sessions/{sid}"), headers=H).json()["id"] == sid, "recuperar sesión")

    # --- balance vacío ------------------------------------------------------
    bal = requests.get(U("/v1/balance"), headers=H).json()
    check(bal["object"] == "balance" and bal["available"][0]["amount"] == 0 and bal["available"][0]["currency"] == "usd", bal)

    # --- una venta interna crea charge con comisión correcta ----------------
    ch = st.record_sale(100.0, "test")     # $100
    fee_expected = round(100 * 0.029 + 0.30, 2)     # $3.20
    check(ch["object"] == "charge" and ch["amount"] == 10000 and ch["status"] == "succeeded", ch)
    check(ch["fee"] == int(round(fee_expected * 100)) and ch["net"] == 10000 - ch["fee"], f"comisión {ch['fee']} esperada {fee_expected}")
    got = requests.get(U(f"/v1/charges/{ch['id']}"), headers=H).json()
    check(got["id"] == ch["id"] and got["amount"] == 10000, "recuperar charge")
    check(len(requests.get(U("/v1/charges"), headers=H).json()["data"]) == 1, "listar charges")

    # balance: el neto está en PENDIENTE (aún no liquidado)
    bal = requests.get(U("/v1/balance"), headers=H).json()
    check(bal["pending"][0]["amount"] == ch["net"] and bal["available"][0]["amount"] == 0, f"pendiente/disponible {bal}")
    txns = requests.get(U("/v1/balance_transactions"), headers=H).json()["data"]
    check(len(txns) == 1 and txns[0]["type"] == "charge" and txns[0]["fee"] == ch["fee"], "balance_transactions")
    check(requests.get(U("/v1/payouts"), headers=H).json()["data"] == [], "sin payouts todavía")
    check(requests.get(U("/v1/charges/ch_nope"), headers=H).status_code == 404, "charge inexistente")

print("STRIPE OK")
