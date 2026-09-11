"""Smoke test del proveedor LLM real (freellmapi) a través del gemelo OpenRouter.

NO es un gate: si el proveedor gratuito está caído/saturado, imprime SKIP y sale 0.
Cuando está disponible, confirma que una chat completion real fluye por el twin,
cobra tokens al precio real y queda en /generation. Lee .env para el upstream.

    python scripts/smoke_provider.py [modelo_openrouter]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _common import LiveApp, make_world  # noqa: E402
from econosim.twins.openrouter import OpenRouterTwin  # noqa: E402
from econosim.upstream import HTTPUpstream  # noqa: E402
from upstream_probe import load_env  # noqa: E402

import requests  # noqa: E402


def main() -> int:
    env = load_env()
    base = env.get("ECONOSIM_UPSTREAM_BASE_URL_LOCAL") or env.get("ECONOSIM_UPSTREAM_BASE_URL")
    key = env.get("ECONOSIM_UPSTREAM_API_KEY")
    if not base or not key:
        print("SKIP: sin ECONOSIM_UPSTREAM_* en .env")
        return 0
    model = sys.argv[1] if len(sys.argv) > 1 else "openai/gpt-oss-120b"
    # ventana corta: el objetivo es 'está vivo ahora mismo?', no aguantar un cold-start largo
    w, h = make_world(initial_eur=50)
    o = OpenRouterTwin(w, HTTPUpstream(base, key, retry_budget_s=90))
    with LiveApp(o.app()) as net:
        t0 = time.time()
        try:
            r = requests.post(net.url("/api/v1/chat/completions"),
                              headers={"Authorization": f"Bearer {o.api_key}", "Content-Type": "application/json"},
                              json={"model": model, "messages": [{"role": "user", "content": "Responde solo: OK"}],
                                    "max_tokens": 10, "usage": {"include": True}}, timeout=120)
        except requests.RequestException as e:
            print(f"SKIP: proveedor inaccesible ({e})")
            return 0
        dt = time.time() - t0
        if r.status_code != 200:
            print(f"SKIP: proveedor no disponible ({r.status_code} en {dt:.0f}s): {r.text[:160]}")
            return 0
        d = r.json()
        u = d["usage"]
        assert d["model"] == model and u["total_tokens"] > 0, d
        assert u["cost"] >= 0 and w.balance() <= 5000, "no cobró la compra de créditos"
        gid = d["id"]
        g = requests.get(net.url("/api/v1/generation"), headers={"Authorization": f"Bearer {o.api_key}"},
                         params={"id": gid}).json()["data"]
        assert g["tokens_prompt"] > 0, g
        print(f"OK proveedor vivo en {dt:.1f}s · modelo={model} · tokens={u['total_tokens']} · "
              f"coste=${u['cost']:.6f} · respuesta={d['choices'][0]['message'].get('content','')[:40]!r}")
        print(f"   compras créditos={o.purchases} saldo={w.balance()/100:.2f} EUR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
