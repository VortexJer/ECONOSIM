"""G1: catálogo real, auth, esquema de chat completion, coste exacto, /generation y /credits."""
from __future__ import annotations

import json

import requests

from _common import ROOT, LiveApp, check, make_world
from econosim.twins.openrouter import OpenRouterTwin
from econosim.upstream import FakeUpstream

snapshot = json.loads((ROOT / "data" / "pricing" / "openrouter_models_raw.json").read_text(encoding="utf-8"))["data"]
cfg = json.loads((ROOT / "data" / "pricing" / "openrouter.json").read_text(encoding="utf-8"))
check(cfg["_source"]["retrieved"] and "openrouter.ai" in cfg["_source"]["catalog"], "catálogo sin fuente")

w, h = make_world(initial_eur=50)
up = FakeUpstream(["Hola, soy tu asistente."], prompt_tokens=1234, completion_tokens=567)
o = OpenRouterTwin(w, up, api_key="sk-or-v1-test")
H = {"Authorization": "Bearer sk-or-v1-test"}
MODEL = "openai/gpt-4o-mini"

with LiveApp(o.app()) as net:
    U = net.url
    # --- catálogo: público, mismos ids y precios que el snapshot -------------------------
    data = requests.get(U("/api/v1/models")).json()["data"]
    check(len(data) == len(snapshot) and len(data) > 300, f"catálogo {len(data)} vs snapshot {len(snapshot)}")
    check([m["id"] for m in data] == [m["id"] for m in snapshot], "ids del catálogo")
    check(all(m["pricing"] == s["pricing"] for m, s in zip(data, snapshot)), "precios del catálogo")
    check(all({"id", "name", "pricing", "context_length", "architecture"} <= set(m) for m in data), "campos del catálogo")
    ep = requests.get(U(f"/api/v1/models/{MODEL}/endpoints")).json()["data"]
    check(ep["id"] == MODEL and ep["endpoints"][0]["pricing"] == o.models[MODEL]["pricing"], "endpoints")
    check(requests.get(U("/api/v1/models/no/such/endpoints")).status_code == 404, "endpoints 404")

    # --- auth ---------------------------------------------------------------------------
    r = requests.get(U("/api/v1/credits"))
    check(r.status_code == 401 and r.json()["error"]["code"] == 401, "sin clave")
    check(requests.get(U("/api/v1/credits"), headers={"Authorization": "Bearer sk-or-v1-bad"}).status_code == 401, "clave mala")
    k = requests.get(U("/api/v1/auth/key"), headers=H).json()["data"]
    check(k["is_free_tier"] is True and k["usage"] == 0 and "rate_limit" in k, k)

    # --- errores de petición --------------------------------------------------------------
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json={"model": "nope/model", "messages": [{"role": "user", "content": "x"}]})
    check(r.status_code == 400 and "not a valid model ID" in r.json()["error"]["message"], r.text)
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json={"model": MODEL})
    check(r.status_code == 400, "sin messages")
    r = requests.post(U("/api/v1/chat/completions"), headers=H, data="{{")
    check(r.status_code == 400, "JSON inválido")

    # --- chat completion: esquema real y coste exacto ------------------------------------------
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hola"}], "usage": {"include": True},
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]}
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json=body)
    check(r.status_code == 200, r.text)
    d = r.json()
    check(d["id"].startswith("gen-") and d["object"] == "chat.completion" and d["model"] == MODEL, d.keys())
    check(d["provider"] and isinstance(d["created"], int), "provider/created")
    ch = d["choices"][0]
    check(ch["message"]["content"] == "Hola, soy tu asistente." and ch["finish_reason"] == "stop"
          and ch["native_finish_reason"] == "stop" and ch["index"] == 0 and ch["logprobs"] is None, ch)
    u = d["usage"]
    check(u["prompt_tokens"] == 1234 and u["completion_tokens"] == 567 and u["total_tokens"] == 1801, u)
    p = o.models[MODEL]["pricing"]
    expected_cost = 1234 * float(p["prompt"]) + 567 * float(p["completion"])
    check(abs(u["cost"] - expected_cost) < 1e-9 and u["is_byok"] is False, f"cost {u['cost']} != {expected_cost}")
    check(expected_cost > 0, "el modelo elegido no cobra")
    # lo que llegó al proveedor real: modelo mapeado, sin campos propios de OpenRouter
    sent = up.calls[-1]
    check(sent["model"] == cfg["upstream"]["default"] or sent["model"] in cfg["upstream"]["map"].values(), sent["model"])
    check("usage" not in sent and sent["messages"] == body["messages"] and sent["tools"] == body["tools"], "payload al upstream")

    # --- créditos: compra automática al primer uso, y gasto exacto --------------------------
    c = requests.get(U("/api/v1/credits"), headers=H).json()["data"]
    check(c["total_credits"] == cfg["auto_topup"]["amount_usd"], c)
    check(abs(c["total_usage"] - expected_cost) < 1e-6, c)
    check(abs(o.credits_usd - (cfg["auto_topup"]["amount_usd"] - expected_cost)) < 1e-9, "saldo de créditos")
    g = requests.get(U("/api/v1/generation"), headers=H, params={"id": d["id"]}).json()["data"]
    check(g["model"] == MODEL and abs(g["total_cost"] - expected_cost) < 1e-9 and g["tokens_prompt"] == 1234, g)
    check(requests.get(U("/api/v1/generation"), headers=H, params={"id": "gen-nope"}).status_code == 404, "generation 404")
    check(requests.get(U("/api/v1/auth/key"), headers=H).json()["data"]["is_free_tier"] is False, "tier tras comprar")

    # --- sin usage.include no aparece el coste (como el real) -------------------------------------
    d2 = requests.post(U("/api/v1/chat/completions"), headers=H, json={"model": MODEL, "messages": [{"role": "user", "content": "x"}]}).json()
    check("cost" not in d2["usage"] and d2["usage"]["total_tokens"] == 1801, d2["usage"])
    check(abs(o.usage_usd - 2 * expected_cost) < 1e-9, "segundo cobro")

    # --- error del proveedor real → 502 con formato OpenRouter, sin cobrar -------------------------
    from econosim.upstream import UpstreamError
    up.script = [UpstreamError(500, "boom")]
    up.calls.clear()
    before = o.usage_usd
    r = requests.post(U("/api/v1/chat/completions"), headers=H, json={"model": MODEL, "messages": [{"role": "user", "content": "x"}]})
    check(r.status_code == 502 and r.json()["error"]["code"] == 502 and "metadata" in r.json()["error"], r.text)
    check(o.usage_usd == before, "cobró una llamada fallida")

    # --- prompt (completions legacy) también funciona ------------------------------------------
    up.script = ["ok"]
    r = requests.post(U("/api/v1/completions"), headers=H, json={"model": MODEL, "prompt": "hola"})
    check(r.status_code == 200 and up.calls[-1]["messages"] == [{"role": "user", "content": "hola"}], "completions legacy")

print("OPENROUTER OK")
