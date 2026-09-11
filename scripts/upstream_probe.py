"""Comprueba el proveedor LLM real (upstream) leyendo .env. Nunca imprime la clave."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("ECONOSIM_")})
    return env


def call(base: str, key: str, path: str, body: dict | None = None, timeout: int = 90):
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


if __name__ == "__main__":
    env = load_env()
    base = env.get("ECONOSIM_UPSTREAM_BASE_URL_LOCAL") or env["ECONOSIM_UPSTREAM_BASE_URL"]
    key = env["ECONOSIM_UPSTREAM_API_KEY"]
    models = call(base, key, "/models")["data"]
    print(f"upstream {base}: {len(models)} modelos; p.ej. {[m['id'] for m in models[:8]]}")
    model = sys.argv[1] if len(sys.argv) > 1 else "auto"
    t0 = time.time()
    r = call(base, key, "/chat/completions", {"model": model, "messages": [{"role": "user", "content": "Responde solo: OK"}], "max_tokens": 10})
    print(f"model={r.get('model')} usage={r.get('usage')} {time.time() - t0:.1f}s -> {r['choices'][0]['message']['content']!r}")
