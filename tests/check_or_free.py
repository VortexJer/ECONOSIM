"""G3: modelos :free — gratis, 50/día (1000 con >= $10 comprados), 20/min, y reset al día siguiente."""
from __future__ import annotations

import json
from datetime import timedelta

import requests

from _common import ROOT, LiveApp, check, make_world
from econosim.twins.openrouter import OpenRouterTwin
from econosim.upstream import FakeUpstream

cfg = json.loads((ROOT / "data" / "pricing" / "openrouter.json").read_text(encoding="utf-8"))["free_models"]
w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream(["ok"], 100, 10), api_key="k")
FREE = next(m for m in o.catalog if m["id"].endswith(":free"))["id"]
PAID = "openai/gpt-4o-mini"
H = {"Authorization": "Bearer k"}


def call(url, model):
    return requests.post(url, headers=H, json={"model": model, "messages": [{"role": "user", "content": "x"}], "usage": {"include": True}})


with LiveApp(o.app()) as net:
    U = net.url("/api/v1/chat/completions")
    # gratis de verdad: ni compra, ni coste, ni cargo en banco
    r = call(U, FREE)
    check(r.status_code == 200 and r.json()["usage"]["cost"] == 0, r.text[:200])
    check(w.balance() == 5000 and o.purchases == [] and o.usage_usd == 0, "un modelo free costó algo")

    # 20 por minuto
    statuses = [call(U, FREE).status_code for _ in range(cfg["per_minute"])]
    check(statuses[:cfg["per_minute"] - 1] == [200] * (cfg["per_minute"] - 1) and statuses[-1] == 429, statuses)
    r = call(U, FREE)
    check(r.status_code == 429 and "free-models-per-min" in r.json()["error"]["message"], r.text)
    w.advance(timedelta(seconds=61))
    check(call(U, FREE).status_code == 200, "no se libera el límite por minuto")

    # 50 por día sin créditos comprados: agotar respetando el límite por minuto
    done = 1 + (cfg["per_minute"] - 1) + 1
    while done < cfg["per_day_without_credits"]:
        w.advance(timedelta(seconds=61))
        r = call(U, FREE)
        check(r.status_code == 200, f"llamada {done + 1}: {r.status_code} {r.text[:150]}")
        done += 1
    w.advance(timedelta(seconds=61))
    r = call(U, FREE)
    check(r.status_code == 429 and "free-models-per-day" in r.json()["error"]["message"], f"petición {done + 1}: {r.text[:200]}")
    hdr = r.json()["error"]["metadata"]["headers"]
    check(hdr["X-RateLimit-Limit"] == str(cfg["per_day_without_credits"]), hdr)
    # los de pago siguen funcionando
    check(call(U, PAID).status_code == 200, "el límite free bloqueó un modelo de pago")

    # al día siguiente (fecha mostrada) vuelve
    w.advance(timedelta(days=1))
    check(call(U, FREE).status_code == 200, "no se reinicia el cupo diario")

    # con >= $10 comprados, el límite sube a 1000
    o.purchased_usd = cfg["credits_threshold_usd"]
    o.credits_usd = 100
    n = 0
    for _ in range(cfg["per_day_without_credits"] + 5):
        w.advance(timedelta(seconds=61))
        if call(U, FREE).status_code == 200:
            n += 1
    check(n == cfg["per_day_without_credits"] + 5, f"con créditos solo pasaron {n}")
    check(o._free_limit() == cfg["per_day_with_credits"], "límite con créditos")

print("FREE OK")
