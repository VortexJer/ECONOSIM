"""G2: el embudo euros->impresiones->clics con CPM/CTR reales, rendimientos decrecientes, atractivo solo al CTR."""
from __future__ import annotations

from _common import ROOT, check
from econosim.ads.funnel import ad_appeal, funnel, load_benchmarks

bench = load_benchmarks(ROOT / "data" / "pricing")
CAT = "digital_product"

# --- impresiones = gasto/CPM*1000 (CPM ajustado por sector) -------------------
p = bench.platform("meta")
mult = bench.sector_mult(CAT)
cpm = p["cpm_usd"] * mult["cpm"]
r = funnel(bench, "meta", CAT, spend_usd=100.0, prior_impressions=0, appeal=1.0)
check(abs(r["impressions"] - 100.0 / cpm * 1000) < 2, f"impresiones {r['impressions']} != {100/cpm*1000:.0f}")
check(r["clicks"] > 0 and r["visits"] == r["clicks"], "clics/visitas")
# CTR efectivo cerca del base × sector al empezar (fatiga ~1)
check(abs(r["effective_ctr"] - p["base_ctr"] * mult["ctr"]) / (p["base_ctr"] * mult["ctr"]) < 0.5, "CTR efectivo inicial")

# --- rendimientos decrecientes: doblar el gasto NO dobla los clics -------------
low = funnel(bench, "meta", CAT, spend_usd=1000.0, prior_impressions=0)
# gastar 2000 de golpe sobre la misma audiencia
high = funnel(bench, "meta", CAT, spend_usd=2000.0, prior_impressions=0)
check(high["clicks"] < 2 * low["clicks"], f"sin rendimientos decrecientes: {low['clicks']:.0f} -> {high['clicks']:.0f}")
# y gastar más tarde (con impresiones acumuladas) rinde menos por euro
early = funnel(bench, "meta", CAT, spend_usd=500.0, prior_impressions=0)
late = funnel(bench, "meta", CAT, spend_usd=500.0, prior_impressions=p["fatigue_half_impressions"] * 3)
check(late["clicks"] < early["clicks"] * 0.5, f"la fatiga debería reducir clics: {early['clicks']:.0f} -> {late['clicks']:.0f}")

# --- el atractivo mueve el CTR (y por tanto los clics), no el CPM --------------
plain = funnel(bench, "meta", CAT, spend_usd=500.0, appeal=ad_appeal(5))    # neutro 1.0
great = funnel(bench, "meta", CAT, spend_usd=500.0, appeal=ad_appeal(10))   # 1.5
poor = funnel(bench, "meta", CAT, spend_usd=500.0, appeal=ad_appeal(0))     # 0.5
check(great["clicks"] > plain["clicks"] > poor["clicks"], "el atractivo no mueve los clics")
check(great["impressions"] == plain["impressions"] == poor["impressions"], "el atractivo NO debe mover las impresiones (solo el CTR)")
check(great["cpm"] == plain["cpm"], "el atractivo no debe cambiar el CPM")

# --- ad_appeal: 5->1.0, 10->1.5, 0->0.5 --------------------------------------
check(abs(ad_appeal(5) - 1.0) < 1e-9 and abs(ad_appeal(10) - 1.5) < 1e-9 and abs(ad_appeal(0) - 0.5) < 1e-9, "ad_appeal mal")

# --- Google es más caro (CPM) pero convierte mejor: comprobamos el CPM --------
gr = funnel(bench, "google", CAT, spend_usd=100.0, prior_impressions=0)
check(gr["cpm"] > r["cpm"], "Google debería tener CPM más alto que Meta")

# gasto cero -> nada
check(funnel(bench, "meta", CAT, spend_usd=0)["clicks"] == 0, "gasto 0 debería dar 0 clics")

print("FUNNEL OK")
