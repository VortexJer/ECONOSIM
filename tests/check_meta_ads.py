"""G3: gemelo Meta Ads — esquema real, auth, campaña con presupuesto diario, gasto cobrado, informe."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import REAL_START, LiveApp, check
from econosim.ledger import to_cents
from econosim.twins.meta_ads import MetaAdsTwin
from econosim.world import World

w = World(REAL_START, initial_eur=100000.0)
meta = MetaAdsTwin(w, access_token="tok123", account_id="555")
ACC = "555"

with LiveApp(meta.app()) as net:
    U = net.url
    A = f"/v22.0/act_{ACC}"
    # --- auth ---------------------------------------------------------------
    check(requests.get(U(A)).status_code == 401, "sin token")
    check(requests.get(U(A), params={"access_token": "bad"}).status_code == 401, "token malo")
    acc = requests.get(U(A), params={"access_token": "tok123"}).json()
    check(acc["id"] == f"act_{ACC}" and acc["currency"] == "USD" and acc["account_status"] == 1, acc)

    # --- crear campaña con presupuesto diario (en centavos, como Meta) -------
    r = requests.post(U(A + "/campaigns"), params={"access_token": "tok123"},
                      data={"name": "Plantillas Q4", "objective": "OUTCOME_TRAFFIC",
                            "daily_budget": "1000", "category": "digital_product"})   # $10/día
    check(r.status_code == 200 and r.json()["id"], r.text)
    cid = r.json()["id"]
    check(requests.post(U(A + "/campaigns"), params={"access_token": "tok123"}, data={"name": "x"}).status_code == 400, "sin presupuesto")

    camps = requests.get(U(A + "/campaigns"), params={"access_token": "tok123"}).json()["data"]
    check(len(camps) == 1 and camps[0]["status"] == "ACTIVE" and camps[0]["daily_budget"] == "1000", camps)

    # --- avanzar 10 días: se cobra el gasto diario y el informe cuadra -------
    bank0 = w.balance()
    w.advance(timedelta(days=10))
    fx = w.load_pricing("fx")["usd_per_eur"]
    spent = [e for e in w.ledger.entries() if e.counterparty == "Meta Platforms, Inc."]
    check(len(spent) == 10, f"deberían ser 10 cobros diarios, hubo {len(spent)}")
    check(all(e.amount_cents == -to_cents(10.0 / fx) for e in spent), "el cargo diario no es $10")
    check(w.balance() == bank0 - to_cents(10.0 / fx) * 10, "saldo tras 10 días de anuncios")

    ins = requests.get(U(f"/v22.0/{cid}/insights"), params={"access_token": "tok123"}).json()["data"][0]
    check(int(ins["impressions"]) > 0 and int(ins["clicks"]) > 0 and float(ins["spend"]) == 100.0, ins)
    check(abs(float(ins["cpm"]) - 7.47 * 0.9) / (7.47 * 0.9) < 0.3, f"CPM del informe {ins['cpm']} lejos del benchmark")
    check(float(ins["ctr"]) > 0, "CTR en el informe")

    # --- pausar la campaña: deja de cobrar ----------------------------------
    requests.post(U(f"/v22.0/{cid}"), params={"access_token": "tok123"}, data={"status": "PAUSED"})
    bank1 = w.balance()
    w.advance(timedelta(days=5))
    check(w.balance() == bank1, "una campaña pausada no debería seguir cobrando")

    # --- sin saldo: la campaña se pausa sola (tarjeta rechazada) -------------
    w2 = World(REAL_START, initial_eur=15.0)
    m2 = MetaAdsTwin(w2, access_token="t")
    m2.mgr.create_campaign("c9", "cara", "digital_product", 10.0)
    w2.advance(timedelta(days=3))     # $10/día; con 15€ solo cubre ~1 día
    check(m2.mgr.campaigns["c9"].status == "PAUSED", "sin saldo la campaña debería pausarse")

print("META ADS OK")
