"""G1: catálogo de cagadas por ámbito, con fuente por entrada; validación."""
from __future__ import annotations

import copy
import json

from _common import ROOT, check
from econosim.hostile.catalog import IncidentCatalog

raw = json.loads((ROOT / "data" / "pricing" / "incidents.json").read_text(encoding="utf-8"))
cat = IncidentCatalog(pricing_dir=ROOT / "data" / "pricing")

# cubre los ámbitos del diseño (§12)
domains = cat.domains()
for d in ("seguridad", "rgpd", "fiscal", "propiedad_intelectual", "consumo", "reguladas", "spam", "plataformas", "penal", "eticas"):
    check(d in domains, f"falta el ámbito {d}")
check(len(cat.keys()) >= 10, f"pocas cagadas: {len(cat.keys())}")

# cada entrada: campos clave + fuente completa con URL
for k in cat.keys():
    inc = cat[k]
    check(inc.domain and inc.label and 0 <= inc.get("base_detection") <= 1, f"{k} campos")
    src = inc.source
    check({"what", "url", "study", "retrieved"} <= set(src) and src["url"].startswith("http"), f"{k} fuente")
    check(inc.get("delay_days") and inc.get("delay_days")[0] <= inc.get("delay_days")[1], f"{k} plazo")

# detección MÁS estricta que la real (§2.7)
check(cat.strictness > 1.0, "la severidad de detección debería ser > 1")

# --- el validador rechaza una entrada sin fuente ------------------------------
bad = copy.deepcopy(raw)
first = next(iter(bad["incidents"]))
del bad["incidents"][first]["source"]
tmp = ROOT / "data" / "pricing" / "_bad_incidents.json"
tmp.write_text(json.dumps(bad), encoding="utf-8")
try:
    IncidentCatalog(path=tmp)
    check(False, "debería rechazar sin fuente")
except ValueError:
    pass
finally:
    tmp.unlink()

print("INCIDENT CATALOG OK")
