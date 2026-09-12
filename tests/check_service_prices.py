"""G1: tabla de precios de servicios con fuente; validación."""
from __future__ import annotations

import json

from _common import ROOT, check

cfg = json.loads((ROOT / "data" / "pricing" / "services.json").read_text(encoding="utf-8"))
src = cfg["_source"]
for k in ("domains_url", "email_url", "betting_url", "retrieved"):
    check(src.get(k), f"falta fuente {k}")
check(all(str(src[k]).startswith("http") for k in ("domains_url", "email_url", "betting_url")), "fuentes sin URL")

d = cfg["domains"]
check(d["tld_price_usd"]["com"] > 0 and d["tld_price_usd"]["io"] > d["tld_price_usd"]["com"], "precios de dominio irreales")
check(d["default_price_usd"] > 0 and d["renewal_grace_days"] > 0, "config de dominios")

e = cfg["email"]
check(e["free_per_month"] >= 1000 and e["price_per_email_usd"] > 0, "config de correo")
check(isinstance(e["bounce_domains"], list) and e["bounce_domains"], "sin dominios de rebote")

b = cfg["betting"]
check(0 < b["house_margin"] < 0.2 and b["settle_delay_days"] >= 0, "config de apuestas")

print("SERVICE PRICES OK")
