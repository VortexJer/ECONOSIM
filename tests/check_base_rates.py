"""G1: tablas de tasas base con fuente por categoría; el cargador valida (sin fuente = error)."""
from __future__ import annotations

import copy
import json

from _common import ROOT, check
from econosim.resolver.rates import BaseRates

raw = json.loads((ROOT / "data" / "pricing" / "base_rates.json").read_text(encoding="utf-8"))
br = BaseRates()

check(len(br.keys()) >= 5, f"pocas categorías: {br.keys()}")
check(br.currency == "USD", "moneda")

# cada categoría: label + fuente completa (what/url/study/retrieved) con URL http
for key in br.keys():
    cat = br[key]
    check(cat.label, f"{key} sin label")
    src = cat.source
    check({"what", "url", "study", "retrieved"} <= set(src), f"{key} fuente incompleta")
    check(src["url"].startswith("http") and src["retrieved"], f"{key} fuente sin url/fecha")

# las categorías de venta tienen distribución de cola pesada bien formada
for key in br.keys():
    cat = br[key]
    ln = cat.get("units_lognormal")
    if ln:
        check(ln["median"] > 0 and ln["sigma"] > 0, f"{key}: lognormal inválida")
        check(0.0 <= cat.get("p_zero", 0) < 1.0, f"{key}: p_zero fuera de rango")
        check(cat.get("median_price", 0) > 0, f"{key}: sin precio mediano")

# modulación presente
for k in ("quality", "marketing", "saturation"):
    check(k in br.modulation, f"falta modulación {k}")

# --- el validador RECHAZA una tabla sin fuente (control) ---------------------
bad = copy.deepcopy(raw)
first = next(iter(bad["categories"]))
del bad["categories"][first]["source"]
tmp = ROOT / "data" / "pricing" / "_bad_rates_test.json"
tmp.write_text(json.dumps(bad), encoding="utf-8")
try:
    BaseRates(tmp)
    check(False, "debería rechazar una categoría sin fuente")
except ValueError as e:
    check("fuente" in str(e).lower(), f"error inesperado: {e}")
finally:
    tmp.unlink()

# fuente sin URL http también se rechaza
bad2 = copy.deepcopy(raw)
bad2["categories"][first]["source"]["url"] = "ftp://x"
tmp.write_text(json.dumps(bad2), encoding="utf-8")
try:
    BaseRates(tmp)
    check(False, "debería rechazar fuente sin URL http")
except ValueError:
    pass
finally:
    tmp.unlink()

print("BASE RATES OK")
