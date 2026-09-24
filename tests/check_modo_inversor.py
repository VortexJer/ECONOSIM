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

# --- lo que la IA puede leer no sabe que existe lo desactivado (fase 14) --------
# /opt/agent es la carpeta agent/ entera: ni el encargo, ni el catálogo, ni el código
# pueden nombrar la tienda, los anuncios, los dominios, el correo o las apuestas.
from pathlib import Path                                      # noqa: E402
RAIZ = Path(__file__).resolve().parent.parent
PROHIBIDO = ("stripe", "facebook", "googleads", "google ads", "porkbun", "resend", "odds",
             "anuncio", "dominio", "correo", "apuesta", "tienda", "cliente", "emprendedor", "inversor")
for f in (RAIZ / "agent").rglob("*"):
    if f.is_file() and "__pycache__" not in f.parts:
        txt = f.read_text(encoding="utf-8", errors="ignore").lower()
        malas = [p for p in PROHIBIDO if p in txt]
        check(not malas, f"{f.name} delata lo desactivado: {malas}")
sys.path.insert(0, str(RAIZ / "agent"))
import importlib                                             # noqa: E402
os.environ["OPENROUTER_API_KEY"] = "x"
agent = importlib.import_module("agent")
inv = agent.briefing()
check("invertirlo" in inv or "invertir" in inv, "el encargo no habla de invertir")
check("TESIS" in inv, "un inversor sin tesis escrita no aprende nada")
check("comisión" in inv.lower() and "tasas" in inv.lower(), "no le avisa de lo que cuesta operar")
servicios = (RAIZ / "agent" / "SERVICIOS.md").read_text(encoding="utf-8")
check("options/contracts" in servicios and "gamma" in servicios and "technical_indicator" in servicios,
      "el catálogo no cuenta las opciones y los indicadores")
# desactivado, NO borrado: el modo completo sigue en disco y es recuperable
for f in ("SYSTEM.md", "SERVICIOS.md"):
    check((RAIZ / "desactivado" / "agent" / f).exists(), f"se perdió el {f} del modo completo")
check("api.stripe.com" in (RAIZ / "desactivado" / "agent" / "SERVICIOS.md").read_text(encoding="utf-8"),
      "el catálogo completo guardado no es el completo")
# y el mundo arranca en solo inversión SIN pedirlo
import econosim.run as _run                                   # noqa: E402
import inspect                                                # noqa: E402
src = inspect.getsource(_run.main)
check('os.environ.get("ECONOSIM_SOLO_INVERSION", "1")' in src, "el defecto del mundo no es solo inversión")
# el conmutador ida y vuelta, sobre una copia (no toca el proyecto)
import shutil, tempfile                                       # noqa: E402
sys.path.insert(0, str(RAIZ / "scripts"))
modo = importlib.import_module("modo")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    shutil.copytree(RAIZ / "agent", td / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(RAIZ / "desactivado", td / "desactivado")
    modo.ROOT, modo.AGENT, modo.OFF = td, td / "agent", td / "desactivado" / "agent"
    check(modo.activo() == "inversor", "el modo activo debería ser inversor")
    modo.main(["completo"])
    check(modo.activo() == "completo", "no pasó a completo")
    modo.main(["inversor"])
    check(modo.activo() == "inversor", "no volvió a inversor")
    check((td / "agent" / "SYSTEM.md").read_text(encoding="utf-8") == inv, "la vuelta alteró el encargo")

# --- el acelerador del reloj no puede volver a colarse ----------------------
# Acelerar el reloj MIENTRAS la IA trabaja rompe todos los plazos dentro de su
# contenedor (su reloj es el del mundo): con x60, dos segundos reales son 119 para ella.
# Pasó de verdad: cinco meses sin poder hablar con el bróker y muerta sin comprar nada.
from econosim.world import World                              # noqa: E402
from datetime import datetime, timezone                       # noqa: E402
os.environ.pop("ECONOSIM_ACTIVE_SPEED_FORZAR", None)
try:
    World(datetime(2020, 1, 1, tzinfo=timezone.utc), active_speed=60)
    check(False, "acelerar el reloj en activo debería estar prohibido")
except ValueError as err:
    check("plazos" in str(err), f"el error no explica el motivo: {err}")
World(datetime(2020, 1, 1, tzinfo=timezone.utc), active_speed=1.0)      # x1 siempre vale
os.environ["ECONOSIM_ACTIVE_SPEED_FORZAR"] = "1"
World(datetime(2020, 1, 1, tzinfo=timezone.utc), active_speed=60)       # a sabiendas, se deja
os.environ.pop("ECONOSIM_ACTIVE_SPEED_FORZAR")

print("MODO INVERSOR OK")
