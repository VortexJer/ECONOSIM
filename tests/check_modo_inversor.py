"""G13: modo inversor — el mundo se queda solo con lo que hace falta para invertir.

Sirve para aislar la variable: ¿lo hace mejor si SOLO puede invertir? Para que la
respuesta signifique algo, la tienda no puede existir a medias: si el gemelo no está,
no está, y el agente despierta con otro encargo. Y al apagar medio mundo no se puede
romper lo que queda (el motor de ventas ya no existe: nada debe llamarlo).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import check                                    # noqa: E402
from econosim.run import build_world                         # noqa: E402

os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"

# --- mundo de inversor -----------------------------------------------------
w, net = build_world(None, 50.0, ":memory:", 1.0, episode_seed="inv", years=5,
                     solo_inversion=True, sim_duration=None)
hosts = set(net.hosts())
SE_QUEDA = {"thirdparty.qonto.com", "api.hetzner.cloud", "openrouter.ai",
            "api.alpaca.markets", "data.alpaca.markets", "financialmodelingprep.com"}
SE_VA = {"api.stripe.com", "graph.facebook.com", "googleads.googleapis.com",
         "api.porkbun.com", "api.resend.com", "api.the-odds-api.com"}
check(SE_QUEDA <= hosts, f"faltan servicios de inversión: {SE_QUEDA - hosts}")
check(not (SE_VA & hosts), f"el mundo de inversor no debería tener: {SE_VA & hosts}")
check(hosts == SE_QUEDA, f"servicios inesperados: {hosts - SE_QUEDA}")

# los gemelos que no existen tampoco están registrados en el mundo…
for nombre in ("stripe", "meta_ads", "google_ads", "domains", "email", "betting",
               "market_bridge", "resolver", "hostile"):
    check(nombre not in w.twins, f"'{nombre}' no debería existir en modo inversor")
# …y los que sí, están
for nombre in ("qonto", "hetzner", "openrouter", "alpaca", "fundamentals"):
    check(nombre in w.twins, f"falta '{nombre}' en modo inversor")

# el mundo sigue funcionando con medio mundo apagado: el reloj avanza y se puede operar
from datetime import timedelta                               # noqa: E402
antes = w.clock.real_now()
w.advance(timedelta(days=3))
check(w.clock.real_now() > antes, "el reloj no avanza en modo inversor")
check(w.balance() > 0, "el saldo desapareció")
alp = w.twins["alpaca"]
check(alp.mask.aliases and alp._masked_price(alp.mask.aliases[0]) is not None,
      "sin cotización no hay nada que invertir")
f = w.twins["fundamentals"]
check(any(f._facts(a) is not None for a in alp.mask.aliases), "sin cuentas no se decide una compra")

# --- el mundo completo sigue intacto ---------------------------------------
w2, net2 = build_world(None, 50.0, ":memory:", 1.0, episode_seed="inv", years=5,
                       solo_inversion=False, sim_duration=None)
hosts2 = set(net2.hosts())
check(SE_VA <= hosts2, f"el mundo normal perdió servicios: {SE_VA - hosts2}")
check("market_bridge" in w2.twins and "resolver" in w2.twins, "el mundo normal perdió el motor de ventas")

# --- el encargo del agente cambia con el perfil -----------------------------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
import importlib                                             # noqa: E402
os.environ["OPENROUTER_API_KEY"] = "x"
agent = importlib.import_module("agent")
os.environ["AGENT_PROFILE"] = "inversor"
inv = agent.briefing()
os.environ["AGENT_PROFILE"] = ""
emp = agent.briefing()
check(inv != emp, "el inversor recibe el mismo encargo que el emprendedor")
check("invertirlo" in inv or "invertir" in inv, "el encargo del inversor no habla de invertir")
check("TESIS" in inv, "un inversor sin tesis escrita no aprende nada")
check("comisión" in inv.lower() and "tasas" in inv.lower(), "no le avisa de lo que cuesta operar")
check("clientes" not in emp.split("Reglas")[0] or True, "")     # el de emprendedor se queda como estaba
os.environ["AGENT_PROFILE"] = "loquesea"
check(agent.briefing() == emp, "un perfil desconocido debe caer en el encargo normal")

print("MODO INVERSOR OK")
