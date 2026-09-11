"""G2: auto-recarga con comisión Stripe y cambio real; 402 sin saldo en el banco; tier."""
from __future__ import annotations

import json

import requests

from _common import ROOT, LiveApp, check, make_world
from econosim.ledger import to_cents
from econosim.twins.openrouter import OpenRouterTwin, COUNTERPARTY
from econosim.upstream import FakeUpstream

cfg = json.loads((ROOT / "data" / "pricing" / "openrouter.json").read_text(encoding="utf-8"))
fx = json.loads((ROOT / "data" / "pricing" / "fx.json").read_text(encoding="utf-8"))
check(fx["_source"]["url"].startswith("https://data-api.ecb.europa.eu") and fx["_source"]["retrieved"], "fx sin fuente")
MODEL = "openai/gpt-4o-mini"
MSG = {"model": MODEL, "messages": [{"role": "user", "content": "x"}]}


def expected_eur_cents(amount_usd: float) -> int:
    fee = max(amount_usd * cfg["purchase_fee_rate"], cfg["purchase_fee_min_usd"])
    return to_cents((amount_usd + fee) / fx["usd_per_eur"] * (1 + fx["card_fx_markup"]))


amount = cfg["auto_topup"]["amount_usd"]
check(amount * cfg["purchase_fee_rate"] < cfg["purchase_fee_min_usd"], "el test debe ejercitar la comisión mínima de $0.80")

# --- A: primera llamada → compra automática, un cargo exacto en el banco ---------------------
w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream(["ok"], 100, 10), api_key="k")
H = {"Authorization": "Bearer k"}
with LiveApp(o.app()) as net:
    U = net.url
    check(w.balance() == 5000 and requests.get(U("/api/v1/credits"), headers=H).json()["data"]["total_credits"] == 0, "estado inicial")
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json=MSG)
    check(r.status_code == 200, r.text)
    charges = [e for e in w.ledger.entries() if e.amount_cents < 0]
    check(len(charges) == 1 and charges[0].counterparty == COUNTERPARTY, charges)
    check(charges[0].amount_cents == -expected_eur_cents(amount), f"{charges[0].amount_cents} != {-expected_eur_cents(amount)}")
    check("1998" not in charges[0].display_ts, "fecha real en el asiento")
    check(o.purchases[-1]["fee_usd"] == cfg["purchase_fee_min_usd"], o.purchases[-1])
    check(o.purchased_usd == amount and 0 < o.credits_usd < amount, "créditos tras compra")
    # cien llamadas más no recargan mientras haya saldo por encima del umbral
    for _ in range(100):
        requests.post(U("/api/v1/chat/completions"), headers=H, json=MSG)
    check(len(o.purchases) == 1, f"recargas de más: {len(o.purchases)}")

# --- B: una llamada carísima deja el saldo negativo: 402 con recarga en cada intento hasta volver a positivo
w, h = make_world(initial_eur=50)
BIG = "anthropic/claude-sonnet-4.5"
o = OpenRouterTwin(w, FakeUpstream(["ok"], 1_000_000, 1_000_000), api_key="k")   # 1M+1M tokens por llamada
per_call = o.cost_of(o.models[BIG], 1_000_000, 1_000_000)
check(per_call > 3 * amount, f"el test necesita una llamada que cueste más de 3 recargas (${per_call})")
with LiveApp(o.app()) as net:
    U = net.url
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json={**MSG, "model": BIG})
    check(r.status_code == 200 and len(o.purchases) == 1 and o.credits_usd < 0, "la llamada cara debía pasar y dejar deuda")
    statuses, purchases = [], []
    while o.credits_usd <= 0:
        statuses.append(requests.post(U("/api/v1/chat/completions"), headers=H, json=MSG).status_code)
        purchases.append(len(o.purchases))
        check(len(statuses) < 20, "no sale de la deuda")
    check(all(s == 402 for s in statuses[:-1]) and statuses[-1] == 200, statuses)
    check(purchases == list(range(2, 2 + len(purchases))), f"una recarga por intento: {purchases}")
    check(len([e for e in w.ledger.entries() if e.amount_cents < 0]) == len(o.purchases), "cargos en banco")

# --- C: banco sin saldo → 402, sin cargo, y se recupera al recibir dinero ------------------------------
w, h = make_world(initial_eur=1)      # 1 EUR: no cubre la recarga mínima
o = OpenRouterTwin(w, FakeUpstream(["ok"], 100, 10), api_key="k")
with LiveApp(o.app()) as net:
    U = net.url
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json=MSG)
    check(r.status_code == 402 and r.json()["error"]["code"] == 402 and "Insufficient credits" in r.json()["error"]["message"], r.text)
    check(w.balance() == 100 and o.credits_usd == 0 and o.purchases[-1]["paid"] is False, "cobró sin poder")
    check(requests.get(U("/api/v1/auth/key"), headers=H).json()["data"]["is_free_tier"] is True, "tier sin comprar")
    w.receive(2000, "Ingreso", "cliente")
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json=MSG)
    check(r.status_code == 200 and o.purchases[-1]["paid"] is True, "no se recuperó con saldo")
    check(w.balance() == 2100 - expected_eur_cents(amount), "cargo tras recuperarse")

# --- D: con auto-recarga desactivada, 402 aunque haya banco --------------------------------------------
w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream(["ok"], 100, 10), api_key="k")
o.cfg["auto_topup"]["enabled"] = False
with LiveApp(o.app()) as net:
    r = requests.post(net.url("/api/v1/chat/completions"), headers=H, json=MSG)
    check(r.status_code == 402 and w.balance() == 5000, "sin auto-recarga debe ser 402")

print("TOPUP OK")
