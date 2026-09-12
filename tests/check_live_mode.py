"""G1: modo en vivo — datos REALES entran y NINGUNA acción sale (PROYECTO.md §2.6, §16.13).

Comprueba dos invariantes:
  * Datos reales: sin máscara (símbolos, precios y fecha reales; el tiempo no se desplaza).
  * Candado de egreso: cada acción con efecto externo (orden, apuesta, correo, dominio,
    servidor, producto/cobro, anuncio) se acepta de forma benigna PERO no muta el mundo,
    y queda anotada en el diario de egreso. Y con el modo en vivo APAGADO, el candado es
    inerte: una orden real se ejecuta y mueve el saldo.
"""
from __future__ import annotations

import os

os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"   # no tocar el proveedor externo
import requests

from _common import LiveApp, check
from econosim.run import build_world


# ===== 1) DATOS REALES ENTRAN =================================================
w, _ = build_world(None, 5000.0, ":memory:", 300.0, live=True)
check(w.live.enabled, "el candado de egreso debería estar ACTIVO en vivo")
check(w.clock.offset_years == 0, "en vivo el tiempo NO se desplaza (offset 0)")
check(w.clock.display_now == w.clock.real_now(), "en vivo la fecha mostrada == la fecha real")

alp = w.twins["alpaca"]
mask = alp.mask
check("AAPL" in mask.real_symbols, "faltan símbolos reales en el mercado en vivo (AAPL)")
check(mask.to_alias("AAPL") == "AAPL" and mask.to_real("AAPL") == "AAPL",
      "en vivo NO se renombran los símbolos (identidad)")
check(mask._factor["AAPL"] == 1.0, "en vivo los precios NO se indexan a 100 (factor 1.0)")
real_close = mask.data.series["AAPL"].asof(mask.start_day).close
check(abs(mask.index_price("AAPL", real_close) - real_close) < 1e-9,
      "en vivo el precio servido ES el precio real")


# ===== 2) NINGUNA ACCIÓN SALE =================================================
bal0 = w.balance()
expected = 0


def post(twin_name, method, path, **kw):
    """Sirve el app del gemelo y hace una petición HTTP real contra él."""
    with LiveApp(w.twins[twin_name].app()) as srv:
        return requests.request(method, srv.url(path), timeout=5, **kw)


def blocks(label, resp, ok_status=(200, 201)):
    global expected
    expected += 1
    check(resp.status_code in ok_status, f"{label}: respuesta benigna esperada, {resp.status_code}")
    check(w.balance() == bal0, f"{label}: el saldo NO debe cambiar en vivo")
    check(w.live.blocked_count == expected, f"{label}: no quedó anotado en el diario de egreso")


# -- Alpaca: orden aceptada pero NO ejecutada ---------------------------------
r = post("alpaca", "POST", "/v2/orders",
         headers={"APCA-API-KEY-ID": alp.key_id, "APCA-API-SECRET-KEY": alp.secret},
         json={"symbol": "AAPL", "side": "buy", "qty": 1, "type": "market"})
blocks("alpaca.place_order", r)
check(r.json().get("status") == "accepted" and r.json().get("filled_qty") == "0",
      "la orden en vivo debería quedar 'accepted' sin llenado")
check(not alp.positions, "en vivo no debe crearse ninguna posición")

# -- Apuestas: aceptada pero sin cobro ni liquidación -------------------------
bet = w.twins["betting"]
eid = next(iter(bet.events))
r = post("betting", "POST", f"/v4/bets?apiKey={bet.api_key}",
         json={"event_id": eid, "outcome": "home", "stake": 5.0})
blocks("betting.place_bet", r)
check(not bet.bets, "en vivo no debe registrarse ninguna apuesta")

# -- Correo: aceptado pero nada se entrega ni se cobra ------------------------
em = w.twins["email"]
r = post("email", "POST", "/emails",
         headers={"Authorization": f"Bearer {em.api_key}"},
         json={"from": "yo@mi.com", "to": "cliente@x.com", "subject": "Hola"})
blocks("email.send", r)
check(not em.sent and em.owed_usd == 0, "en vivo no debe enviarse ni cobrarse correo")

# -- Dominios: registro aceptado pero sin alta ni cobro -----------------------
dom = w.twins["domains"]
r = post("domains", "POST", "/api/json/v3/domain/register/mitienda-live.com",
         json={"apikey": dom.apikey, "secretapikey": dom.secret})
blocks("domains.register", r)
check(not dom.owned, "en vivo no debe registrarse ningún dominio")

# -- Hetzner: crear servidor bloqueado (solo queda el VPS propio) -------------
het = w.twins["hetzner"]
n_before = len([s for s in het.servers.values() if not s.deleted])
r = post("hetzner", "POST", "/v1/servers",
         headers={"Authorization": f"Bearer {het.token}"},
         json={"name": "web-2", "server_type": "cx23", "image": "debian-12"})
blocks("hetzner.create_server", r)
check(len([s for s in het.servers.values() if not s.deleted]) == n_before,
      "en vivo no debe aprovisionarse ningún servidor")

# -- Stripe: producto y sesión bloqueados, nada se registra -------------------
stripe = w.twins["stripe"]
r = post("stripe", "POST", "/v1/products",
         headers={"Authorization": f"Bearer {stripe.secret_key}"},
         json={"name": "Kit"})
blocks("stripe.create_product", r)
check(not stripe.products, "en vivo no debe registrarse ningún producto")
r = post("stripe", "POST", "/v1/checkout/sessions",
         headers={"Authorization": f"Bearer {stripe.secret_key}"},
         json={"mode": "payment"})
blocks("stripe.create_session", r)
check(not stripe.sessions, "en vivo no debe abrirse ninguna sesión de pago")

# -- Meta Ads: campaña bloqueada, sin gasto -----------------------------------
meta = w.twins["meta_ads"]
r = post("meta_ads", "POST", f"/v22.0/act_123/campaigns?access_token={meta.token}",
         data={"name": "Lanzamiento", "daily_budget_usd": "8"})
blocks("meta_ads.create_campaign", r)
check(not meta.mgr.campaigns, "en vivo no debe crearse ninguna campaña")

# el diario de egreso tiene una entrada por acción, cada una con servicio y operación
check(len(w.live.journal) == expected, "el diario de egreso no cuadra")
for j in w.live.journal:
    check(j.get("service") and j.get("op") and "ts" in j, "entrada de egreso incompleta")
check(w.balance() == bal0, "tras todo el egreso en vivo, el saldo sigue intacto")


# ===== 3) REGRESIÓN: apagado, el candado es INERTE ============================
w2, _ = build_world(None, 5000.0, ":memory:", 300.0, live=False)
check(not w2.live.enabled, "fuera del modo en vivo el candado está apagado")
alp2 = w2.twins["alpaca"]
b0 = w2.balance()
alias2 = alp2.mask.to_alias("AAPL")   # fuera de vivo los símbolos van enmascarados
with LiveApp(alp2.app()) as srv:
    r = requests.post(srv.url("/v2/orders"),
                      headers={"APCA-API-KEY-ID": alp2.key_id, "APCA-API-SECRET-KEY": alp2.secret},
                      json={"symbol": alias2, "side": "buy", "qty": 1, "type": "market"}, timeout=5)
check(r.status_code == 200 and r.json().get("status") == "filled",
      "sin modo en vivo la orden SÍ se ejecuta (filled)")
check(w2.balance() < b0, "sin modo en vivo la compra SÍ mueve el saldo")
check(w2.live.blocked_count == 0, "sin modo en vivo no se bloquea nada")

print("LIVE MODE OK")
