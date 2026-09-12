"""G3: la nota del juez entra como calidad en el resolutor; el juez pone nota, no dinero."""
from __future__ import annotations

import statistics

from _common import REAL_START, check
from econosim.judge.judge import Judge, Deliverable
from econosim.judge.review import HumanReviewQueue
from econosim.resolver.engine import ActionResolver
from econosim.resolver.rates import BaseRates
from econosim.world import World

br = BaseRates()
BODY = "contenido real del entregable " * 30      # > umbral


def judge_giving(score_dims):
    return Judge(lambda s, u, m: {"dimensions": score_dims, "justification": "x"})


def fresh(judge):
    w = World(REAL_START, initial_eur=1e7, episode_id="EPJ")
    return w, ActionResolver(w, br, judge=judge, review_queue=HumanReviewQueue())


HI = {"completeness": 9, "usefulness": 9, "correctness": 9, "polish": 9, "differentiation": 8}
LO = {"completeness": 2, "usefulness": 2, "correctness": 3, "polish": 2, "differentiation": 1}

# --- la calidad usada en el muestreo viene del juez, no del parámetro ---------
w, r = fresh(judge_giving(HI))
f, o = r.submit("vendo una plantilla", "act-hi", quality=1.0, price=13.0,   # quality=1 debería IGNORARSE
                deliverable=Deliverable(kind="plantilla", content=BODY))
check(r.actions[-1]["eff_quality"] > 7, f"la calidad debería venir del juez (alta), fue {r.actions[-1]['eff_quality']}")

# --- mejor nota del juez -> mejor conversión (sobre muchas realizaciones) -----
def mean_units(dims, n=400):
    total = 0
    for i in range(n):
        w = World(REAL_START, initial_eur=1e7, episode_id=f"EP-{i}")
        rr = ActionResolver(w, br, judge=judge_giving(dims))
        _, o = rr.submit("vendo una plantilla", f"a-{i}", price=13.0,
                         deliverable=Deliverable(kind="plantilla", content=BODY))
        total += o.units
    return total / n


hi = mean_units(HI)
lo = mean_units(LO)
check(hi > lo * 1.4, f"mejor nota del juez debería vender más: alta {hi:.1f} vs baja {lo:.1f}")

# --- el juez NO decide el dinero: misma nota, resultados distintos ------------
units = []
for i in range(300):
    w = World(REAL_START, initial_eur=1e7, episode_id=f"D-{i}")
    rr = ActionResolver(w, br, judge=judge_giving(HI))
    _, o = rr.submit("vendo una plantilla", f"x-{i}", price=13.0,
                     deliverable=Deliverable(kind="plantilla", content=BODY))
    units.append(o.units)
check(len(set(units)) > 20, "con la misma nota, el resultado debería variar (manda el dado)")
zeros = sum(1 for u in units if u == 0)
check(zeros > 0, "ni con buena nota se garantizan ventas: debería haber ceros")

# --- el veredicto queda en la cola de revisión --------------------------------
w, r = fresh(judge_giving(HI))
r.submit("vendo una plantilla", "rev-1", price=13.0, deliverable=Deliverable(kind="plantilla", content=BODY))
check(len(r.review_queue.pending()) == 1 and r.review_queue.items["rev-1"].judge_score > 7, "no se registró en la cola")

print("JUDGE RESOLVER OK")
