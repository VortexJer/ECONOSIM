"""G5 (fase 14): la vida del cerebro Laya es honesta — mismo mundo, sin futuro, sin fugas.

  * Se corre con el entorno de entrenamiento (training/.venv): necesita el tokenizador.
  * La decisión del día d se ejecuta en la sesión SIGUIENTE (nunca al precio con el que decide).
  * El estado no contiene el símbolo real ni el año real, y cabe entero en la ventana de
    Laya (si se truncase, las últimas líneas —posición y caja— desaparecerían en silencio).
  * El dinero pasa por el libro real: Hetzner cobra cada día y las órdenes pagan comisión.
  * Con la regla de tesorería, invertirlo todo en el índice no muere de impago.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "training"))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np                                                   # noqa: E402

from _common import check                                            # noqa: E402
from laya_life import LayaLife, MarketData, Fundamentals             # noqa: E402

md, fu = MarketData(), Fundamentals()

# política que compra siempre: fuerza órdenes para poder auditar cuándo se ejecutan
siempre_compra = lambda states: np.tile([0.0, 0.0, 1.0], (len(states), 1))
L = LayaLife(md, fu, "audit", date(2016, 5, 2), sessions=30)
executed: list[tuple[str, date]] = []
orig = L._order


def spy(alias, side, qty):
    ok = orig(alias, side, qty)
    if ok:
        executed.append((alias, L.a._session_asof()))
    return ok


L._order = spy
r = L.run(siempre_compra)
decididas = {(d.alias, d.day) for d in r.decisions}
check(r.decisions and executed, "no hubo decisiones u órdenes que auditar")
for alias, dia_ejec in executed:
    previas = [d for (a, d) in decididas if a == alias and d < dia_ejec]
    check(previas, f"orden de {alias} el {dia_ejec} sin decisión en una sesión ANTERIOR")
    check((alias, dia_ejec) not in decididas or any(d < dia_ejec for d in previas),
          "se ejecutó al precio del mismo día en que se decidió")

# sin fugas: ni símbolo real ni año real en lo que ve Laya
reales = set(L.mask.real_symbols)
for d in r.decisions:
    palabras = set(d.state.replace(",", " ").replace(".", " ").split())
    check(not (palabras & reales), f"símbolo real en el estado: {palabras & reales}")
    check(str(d.day.year) not in d.state, "año real en el estado")
    check(d.utils is not None and len(d.utils) == 3, "decisión sin etiqueta")

# el dinero pasa por el libro real
conceptos = [e.concept for e in L.w.ledger.entries()]
check(any(c.startswith("Hetzner") for c in conceptos), "Hetzner no cobró")
check(any(c.startswith("Comisión") for c in conceptos), "las órdenes no pagaron comisión")

# cabe en la ventana de Laya
from laya_brain import LayaPolicy                                     # noqa: E402
pol = LayaPolicy(device="cpu")
room = pol.max_len - pol.head_max_len - 4
mx = max(pol.n_tokens(d.state) for d in r.decisions)
check(mx <= room, f"estado de {mx} tokens > {room}: Laya perdería la posición y la caja")
b = pol.encode([r.decisions[0].state])
check(b["input_ids"].shape[1] <= pol.max_len, "secuencia más larga que la ventana")

# tesorería: el índice comprado con todo el dinero libre no muere por no vender
I = LayaLife(md, fu, "treasury", date(2014, 1, 6), sessions=126).run(None, fixed="index")
check(I.alive, "el índice murió de impago con dinero en acciones")
print(f"LAYA LIFE OK (estado máx {mx} tokens de {room})")
