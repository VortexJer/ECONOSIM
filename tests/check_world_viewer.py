"""G9: el visor del mundo — el humano puede abrir la simulación por dentro.

Abrir los servicios como los ve la IA (firmados, sin tener que saber credenciales),
y levantar la cortina: qué fecha real hay detrás y qué empresa es cada alias. Dos
cosas que tienen que cumplirse: que funcione, y que sea SOLO LECTURA (una ventana,
no un mando: desde aquí no se puede mover ni un euro).
"""
from __future__ import annotations

import json

import requests

from _common import LiveApp, check, make_market_world
from econosim.control import control_app
from econosim.fakenet.server import FakeNet
from econosim.market.fundamentals import Fundamentals
from econosim.twins.fundamentals_api import FundamentalsTwin

w, md, mask, alp = make_market_world(seed="visor", years=5, initial_eur=50000.0)
fund = Fundamentals()
tw = FundamentalsTwin(w, md, mask, fund)
net = FakeNet(hang_seconds=1.0, display_now=lambda: w.clock.display_now)
net.mount(alp.host, alp.app())
net.mount(alp.data_host, alp.data_app())
net.mount(tw.host, tw.app())
w.register("fakenet", net)

from _common import LiveNet  # noqa: E402

with LiveNet(net), LiveApp(control_app(w, debug=False)) as ctl:
    T = ctl.url

    # --- qué hay para mirar ------------------------------------------------
    srv = requests.get(T("/world/services")).json()
    hosts = [s["host"] for s in srv["servicios"]]
    check(tw.host in hosts and alp.host in hosts, f"faltan servicios: {hosts}")
    check(srv["simbolos"] and all(s == s.upper() for s in srv["simbolos"]), "sin símbolos")
    fmp = next(s for s in srv["servicios"] if s["host"] == tw.host)
    check(any("ratios-ttm" in r for r in fmp["rutas"]), "sin rutas de ejemplo para empezar")

    alias = srv["simbolos"][0]

    # --- abrir un servicio como lo ve la IA, sin saber la clave -------------
    r = requests.get(T("/world/get"), params={"url": f"{tw.host}/api/v3/ratios-ttm/{alias}"}).json()
    check(r.get("status") == 200, f"el visor no pudo abrir el servicio: {r}")
    cuerpo = json.loads(r["body"])
    check(isinstance(cuerpo, list), "la respuesta no es la del gemelo")

    # con parámetros en la url, que es como se navega de verdad
    r = requests.get(T("/world/get"),
                     params={"url": f"http://{tw.host}/api/v3/income-statement/{alias}?period=quarter&limit=3"}).json()
    check(r.get("status") == 200, f"con query no funciona: {r}")
    check(len(json.loads(r["body"])) <= 3, "el parámetro limit no llegó al gemelo")

    # la cuenta de bolsa también, con sus dos cabeceras
    r = requests.get(T("/world/get"), params={"url": f"{alp.host}/v2/account"}).json()
    check(r.get("status") == 200 and "equity" in r["body"], f"la cuenta de bolsa no abre: {r}")

    # un servicio que no existe en este mundo se dice claramente
    r = requests.get(T("/world/get"), params={"url": "banco-inventado.com/v1/x"})
    check(r.status_code == 404 and "no existe" in r.json().get("error", ""), "host inventado mal tratado")
    check(requests.get(T("/world/get")).status_code == 400, "sin url debería quejarse")

    # --- SOLO LECTURA: el visor no mueve dinero -----------------------------
    saldo = w.balance()
    requests.get(T("/world/get"), params={"url": f"{alp.host}/v2/orders"})
    requests.get(T("/world/get"), params={"url": f"{alp.host}/v2/account"})
    check(w.balance() == saldo, "el visor movió dinero: tiene que ser una ventana, no un mando")
    check(requests.post(T("/world/get"), params={"url": f"{alp.host}/v2/orders"}).status_code in (404, 405),
          "el visor no puede aceptar POST")

    # --- detrás de la cortina ----------------------------------------------
    rev = requests.get(T("/world/reveal")).json()
    check(rev["desfase_años"] > 0, "sin desfase no hay cortina que levantar")
    real_año = int(rev["fecha_real"][:4])
    mostrado_año = int(rev["fecha_mostrada"][:4])
    check(mostrado_año - real_año == rev["desfase_años"], "la cortina no cuadra con el reloj")
    emp = {e["alias"]: e for e in rev["empresas"]}
    check(alias in emp and emp[alias]["real"] == mask.to_real(alias), "el alias no se traduce bien")
    check(emp[alias]["factor"] and emp[alias]["factor"] > 0, "sin factor no se entiende la escala")
    check(len(rev["empresas"]) == len(srv["simbolos"]), "faltan empresas en la traducción")

print("WORLD VIEWER OK")
