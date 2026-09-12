"""G1: tabla de benchmarks publicitarios con fuente; el cargador valida y expone plataformas."""
from __future__ import annotations

import copy
import json

from _common import ROOT, check
from econosim.ads.funnel import load_benchmarks

PD = ROOT / "data" / "pricing"
raw = json.loads((PD / "ads.json").read_text(encoding="utf-8"))
bench = load_benchmarks(PD)

check(set(bench.platforms) == {"meta", "google"}, f"plataformas {bench.platforms}")
for name in bench.platforms:
    p = bench.platform(name)
    for k in ("cpm_usd", "base_ctr", "base_conversion", "fatigue_half_impressions"):
        check(k in p and p[k] > 0, f"{name}: {k} inválido")
    check(0 < p["base_ctr"] < 0.2, f"{name}: CTR base irreal {p['base_ctr']}")

src = raw["_source"]
check(src["meta_url"].startswith("http") and src["google_url"].startswith("http") and src["retrieved"], "sin fuente")
# los números coinciden con los benchmarks citados (Meta CPM 7.47, CTR 1.71%)
check(abs(bench.platform("meta")["cpm_usd"] - 7.47) < 0.01 and abs(bench.platform("meta")["base_ctr"] - 0.0171) < 1e-6, "Meta != benchmark")

# multiplicadores por sector, con default
m = bench.sector_mult("digital_product")
check({"cpm", "ctr", "conversion"} <= set(m), "multiplicador de sector incompleto")
check(bench.sector_mult("categoria_desconocida") == raw["sector_multiplier"]["default"], "sin default de sector")

# --- el validador rechaza una tabla sin fuente / sin campo ---------------------
bad = copy.deepcopy(raw)
del bad["_source"]["meta_url"]
tmp = PD / "_bad_ads_test.json"
tmp.write_text(json.dumps(bad), encoding="utf-8")
try:
    load_benchmarks_bad = __import__("econosim.ads.funnel", fromlist=["load_benchmarks"]).load_benchmarks
    load_benchmarks_bad.__wrapped__ if False else None
    from econosim.ads.funnel import _validate
    _validate(bad)
    check(False, "debería rechazar sin fuente")
except ValueError:
    pass
finally:
    tmp.unlink()

print("AD BENCHMARKS OK")
