"""G4: gemelo Google Ads — customers, campaigns:mutate, searchStream; auth Bearer+developer-token; gasto."""
from __future__ import annotations

from datetime import timedelta

import requests

from _common import REAL_START, LiveApp, check
from econosim.ledger import to_cents
from econosim.twins.google_ads import GoogleAdsTwin
from econosim.world import World

w = World(REAL_START, initial_eur=100000.0)
g = GoogleAdsTwin(w, access_token="ya29.tok", developer_token="dev123", customer_id="1234567890")
CID = "1234567890"
H = {"Authorization": "Bearer ya29.tok", "developer-token": "dev123"}

with LiveApp(g.app()) as net:
    U = net.url
    # --- auth: hacen falta las dos cabeceras --------------------------------
    check(requests.get(U(f"/v17/customers/{CID}")).status_code == 401, "sin auth")
    check(requests.get(U(f"/v17/customers/{CID}"), headers={"Authorization": "Bearer ya29.tok"}).status_code == 401, "sin developer-token")
    cust = requests.get(U(f"/v17/customers/{CID}"), headers=H).json()
    check(cust["id"] == CID and cust["currencyCode"] == "USD", cust)

    # --- crear campaña vía mutate (presupuesto en micros) -------------------
    r = requests.post(U(f"/v17/customers/{CID}/campaigns:mutate"), headers=H,
                      json={"operations": [{"create": {"name": "Búsqueda plantillas",
                                                       "campaignBudgetMicros": 8000000,   # $8/día
                                                       "category": "digital_product"}}]})
    check(r.status_code == 200 and r.json()["results"][0]["resourceName"].startswith(f"customers/{CID}/campaigns/"), r.text)
    gid = r.json()["results"][0]["resourceName"].rsplit("/", 1)[-1]

    # --- avanzar y reportar por searchStream --------------------------------
    bank0 = w.balance()
    w.advance(timedelta(days=7))
    fx = w.load_pricing("fx")["usd_per_eur"]
    spent = [e for e in w.ledger.entries() if e.counterparty == "Google Ads"]
    check(len(spent) == 7 and all(e.amount_cents == -to_cents(8.0 / fx) for e in spent), "cargos diarios de Google")

    r = requests.post(U(f"/v17/customers/{CID}/googleAds:searchStream"), headers=H,
                      json={"query": "SELECT campaign.id, metrics.impressions, metrics.clicks FROM campaign"})
    check(r.status_code == 200, r.text)
    rows = r.json()[0]["results"]
    check(len(rows) == 1 and rows[0]["campaign"]["id"] == gid, "searchStream no devuelve la campaña")
    m = rows[0]["metrics"]
    check(int(m["impressions"]) > 0 and int(m["clicks"]) > 0 and int(m["costMicros"]) == int(round(56.0 * 1e6)), m)
    # Google tiene CPM más alto que Meta: menos impresiones por dólar
    check(int(m["impressions"]) < 56.0 / 7.47 * 1000, "Google debería dar menos impresiones por dólar que Meta")

    # --- pausar vía update --------------------------------------------------
    requests.post(U(f"/v17/customers/{CID}/campaigns:mutate"), headers=H,
                  json={"operations": [{"update": {"resourceName": f"customers/{CID}/campaigns/{gid}", "status": "PAUSED"}}]})
    check(g.mgr.campaigns[gid].status == "PAUSED", "no se pausó")
    bank1 = w.balance()
    w.advance(timedelta(days=4))
    check(w.balance() == bank1, "pausada no debería cobrar")

print("GOOGLE ADS OK")
