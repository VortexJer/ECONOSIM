"""G: el juez EVALÚA de verdad en la ruta del runtime (resolución de ventas en el hilo
del reloj), no solo cuando se le llama desde el hilo principal de un test.

Regresión de un fallo real: `_judge_llm` usaba `get_event_loop().run_until_complete`, que
en el hilo `econosim-clock` (donde corre `_resolve`) y en los handlers async fallaba en
silencio -> el juez caía SIEMPRE a la puntuación base y la calidad del producto no influía
en nada. Este gate crea un producto bueno y uno malo, resuelve las ventas DESDE UN HILO NO
PRINCIPAL (como el servidor) y exige que el juez distinga calidad. Con el bug, ambos caen a
la base y quedan iguales.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import timedelta

os.environ["ECONOSIM_FAKE_UPSTREAM"] = "1"

from _common import LiveApp, check
from econosim.judge import rubric
from econosim import upstream as U

# Upstream que responde como un JUEZ real: dimensiones altas si el contenido es "excelente",
# bajas si es "pobre". (El prompt del juez incluye el contenido del producto.)
_orig = U.FakeUpstream.chat
async def judge_aware_chat(self, payload):
    msgs = payload.get("messages", [])
    text = " ".join(m.get("content", "") for m in msgs)
    if "PRODUCTOEXCELENTE" in text:
        dims = {k: 9 for k in rubric.DIMENSIONS}
    elif "PRODUCTOPOBRE" in text:
        dims = {k: 1 for k in rubric.DIMENSIONS}
    else:
        return await _orig(self, payload)   # no es una petición de juez
    body = json.dumps({"dimensions": dims, "justification": "."})
    return {"choices": [{"message": {"content": body}}], "usage": {}}
U.FakeUpstream.chat = judge_aware_chat

import requests
from econosim.run import build_world

w, _ = build_world(None, 5000.0, ":memory:", 300.0, episode_seed="JRUNTIME", years=5)
stripe = w.twins["stripe"]
mb = w.twins["market_bridge"]
base = w.twins["resolver"].judge.base_score
hdr = {"Authorization": f"Bearer {stripe.secret_key}"}


def make_listing(name, marker):
    with LiveApp(stripe.app()) as s:
        p = requests.post(s.url("/v1/products"), headers=hdr,
                          json={"name": name, "description": f"{marker} " + ("contenido " * 40),
                                "metadata[category]": "digital_product"}, timeout=5).json()
        r = requests.post(s.url("/v1/prices"), headers=hdr,
                          json={"product": p["id"], "unit_amount": 1500, "currency": "usd"}, timeout=5).json()
        return r["id"]


good = make_listing("Bueno", "PRODUCTOEXCELENTE")
poor = make_listing("Malo", "PRODUCTOPOBRE")
check(mb.listings[good]["quality"] is None and mb.listings[poor]["quality"] is None,
      "la calidad debe quedar sin juzgar hasta el primer ciclo (no en el handler)")

# Resolver las ventas DESDE UN HILO NO PRINCIPAL, como hace el reloj del servidor.
err = {}
def advance():
    try:
        w.clock.advance(timedelta(days=16))   # > RESOLVE_EVERY_DAYS: dispara el primer ciclo
    except Exception as e:  # noqa
        err["e"] = repr(e)
t = threading.Thread(target=advance, name="econosim-clock")
t.start(); t.join(15)
check(not err, f"el ciclo de resolución reventó en el hilo del reloj: {err.get('e')}")

gq = mb.listings[good]["quality"]
pq = mb.listings[poor]["quality"]
check(gq is not None and pq is not None, "el juez no puntuó al resolver (quality sigue None)")
# el corazón del gate: el juez DISTINGUE calidad en el runtime. Con el bug ambos caían a la base.
check(gq > pq, f"el juez no distingue bueno ({gq}) de malo ({pq}) — ¿vuelve a caer a la base?")
check(not (gq == base and pq == base), f"ambos cayeron a la puntuación base ({base}): el juez NO se ejecutó")
check(gq > base, f"el producto excelente debería puntuar por encima de la base; gq={gq} base={base}")

print(f"JUDGE RUNTIME OK (bueno={gq} > malo={pq}; base={base})")
