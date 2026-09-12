"""G5: una campaña sube el alcance del producto en el resolutor; sin campaña, orgánico casi nulo; sin fugas."""
from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timedelta

import requests

from _common import REAL_START, LiveNet, check
from econosim.ads.platform import AdManager
from econosim.fakenet.server import FakeNet
from econosim.resolver.engine import ActionResolver
from econosim.resolver.rates import BaseRates
from econosim.twins.meta_ads import MetaAdsTwin
from econosim.world import World

br = BaseRates()
CAT = "digital_product"


def campaign_reach(daily_budget, days, seed="EPADS"):
    w = World(REAL_START, initial_eur=1e9, episode_id=seed)
    meta = MetaAdsTwin(w, access_token="t")
    meta.mgr.create_campaign("c1", "camp", CAT, daily_budget)
    w.advance(timedelta(days=days))
    return w, meta


# --- más presupuesto -> más alcance -> más ventas ----------------------------
def mean_units_with_reach(reach, n=500):
    total = 0
    for i in range(n):
        w = World(REAL_START, initial_eur=1e9, episode_id=f"U-{i}")
        r = ActionResolver(w, br)
        # inyectamos el alcance directamente para aislar el efecto
        _, o = r.submit("vendo una plantilla", f"a-{i}", price=13.0, marketing_reach=reach)
        total += o.units
    return total / n


w_lo, meta_lo = campaign_reach(5.0, 15)     # poco presupuesto
w_hi, meta_hi = campaign_reach(50.0, 15)    # mucho presupuesto
reach_lo = meta_lo.mgr.reach_for(CAT)
reach_hi = meta_hi.mgr.reach_for(CAT)
check(reach_hi > reach_lo > 0, f"más presupuesto debería dar más alcance: {reach_lo:.2f} vs {reach_hi:.2f}")

organic = mean_units_with_reach(0.0)
with_ads = mean_units_with_reach(reach_hi)
check(with_ads > organic * 1.3, f"los anuncios deberían subir ventas: orgánico {organic:.1f} -> con ads {with_ads:.1f}")

# rendimientos decrecientes: 10x presupuesto no da 10x alcance
w_x1, m1 = campaign_reach(5.0, 20, "R1")
w_x10, m10 = campaign_reach(50.0, 20, "R10")
check(m10.mgr.reach_for(CAT) < 10 * m1.mgr.reach_for(CAT), "el alcance debería tener rendimientos decrecientes")

# --- el resolutor toma el alcance del AdManager automáticamente --------------
w = World(REAL_START, initial_eur=1e9, episode_id="EPX")
meta = MetaAdsTwin(w, access_token="t")
meta.mgr.create_campaign("cc", "camp", CAT, 40.0)
w.advance(timedelta(days=15))
r = ActionResolver(w, br, ad_managers=[meta.mgr])
f, o = r.submit("vendo una plantilla", "act-ads", price=13.0)   # sin marketing_reach explícito
check(f.marketing_reach > 0, f"el resolutor debería tomar el alcance de la campaña, fue {f.marketing_reach}")

# --- sin campaña, alcance orgánico nulo --------------------------------------
w2 = World(REAL_START, initial_eur=1e9, episode_id="EPY")
r2 = ActionResolver(w2, br, ad_managers=[AdManager(w2, "meta")])
f2, _ = r2.submit("vendo una plantilla", "act-noads", price=13.0)
check(f2.marketing_reach == 0, "sin campaña no debería haber alcance publicitario")

# --- nodates: los informes no filtran el año real ni la fecha del host -------
HOST = datetime.now()
seen = []
def scan(label, text):
    seen.append(text)
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", text):
        check(m.group(0)[:4] != str(REAL_START.year) and m.group(0) != HOST.strftime("%Y-%m-%d"),
              f"{label}: fuga {m.group(0)}")

wn = World(REAL_START, initial_eur=1e6, episode_id="EPN")
mn = MetaAdsTwin(wn, access_token="tk", account_id="9")
mn.mgr.create_campaign("cid9", "c", CAT, 5.0)
wn.advance(timedelta(days=5))
fn = FakeNet(hang_seconds=1, display_now=lambda: wn.clock.display_now)
fn.mount(mn.host, mn.app())
with LiveNet(fn) as net:
    Hm = {"Host": mn.host}
    for path in (f"/v22.0/act_9?access_token=tk", f"/v22.0/act_9/campaigns?access_token=tk",
                 f"/v22.0/cid9/insights?access_token=tk"):
        resp = requests.get(net.url(path), headers=Hm)
        scan(path, resp.text)
        scan(path + " hdr", json.dumps(dict(resp.headers)))
check(len(seen) >= 5, "pocas respuestas escaneadas")

print("ADS RESOLVER OK")
