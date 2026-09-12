"""G3: sandbox real completo — 11 gemelos alcanzables por TLS, panel con estado/ledger/puntuación."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "sandbox" / "docker-compose.yml")]
ENV = {**os.environ, "ECONOSIM_HANG": "6", "ECONOSIM_SPEED": "1", "ECONOSIM_DEBUG": "0",
       "ECONOSIM_FAKE_UPSTREAM": "1"}
CONTROL = "http://127.0.0.1:8080"

# los 11 dominios gemelos que la IA debe alcanzar por TLS desde dentro
TWIN_HOSTS = [
    "api.hetzner.cloud", "openrouter.ai", "thirdparty.qonto.com",
    "api.alpaca.markets", "data.alpaca.markets", "api.stripe.com",
    "graph.facebook.com", "googleads.googleapis.com",
    "api.porkbun.com", "api.resend.com", "api.the-odds-api.com",
]


def fail(msg):
    print("FAIL:", msg)
    subprocess.run(COMPOSE + ["logs", "--tail", "25"], env=ENV)
    subprocess.run(COMPOSE + ["down", "-v"], env=ENV, capture_output=True)
    sys.exit(1)


def check(cond, msg):
    if not cond:
        fail(msg)


def compose(*a, timeout=600):
    return subprocess.run(COMPOSE + list(a), env=ENV, capture_output=True, text=True, timeout=timeout)


def inside(script, timeout=90):
    r = subprocess.run(COMPOSE + ["exec", "-T", "agent", "bash", "-lc", script], env=ENV,
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


check(subprocess.run(["docker", "info"], capture_output=True).returncode == 0, "Docker no está en marcha")
compose("down", "-v")
r = compose("up", "-d", "--build")
check(r.returncode == 0, f"compose up falló:\n{r.stderr[-1500:]}")

state = None
for _ in range(60):
    try:
        state = requests.get(CONTROL + "/state", timeout=2).json()
        break
    except Exception:
        time.sleep(1)
check(state is not None and state["alive"], "el mundo no responde")

# el VPS terminó de arrancar (CA + credenciales)
for _ in range(60):
    code, out = inside('test -n "$STRIPE_SECRET_KEY" && test -n "$ALPACA_API_KEY_ID" && echo ready')
    if out.endswith("ready"):
        break
    time.sleep(1)
else:
    fail("el VPS no terminó de arrancar")

# --- los 11 gemelos resuelven y responden por TLS (200/40x, nunca cuelgue) ----
probe = " ; ".join(
    f'echo -n "{h}: "; curl -sS -m 12 -o /dev/null -w "%{{http_code}}\\n" https://{h}/ || echo TIMEOUT'
    for h in TWIN_HOSTS)
code, out = inside(probe, timeout=180)
for h in TWIN_HOSTS:
    line = next((l for l in out.splitlines() if l.startswith(h + ":")), "")
    codev = line.split(":")[-1].strip()
    check(codev.isdigit(), f"{h} no respondió por TLS: {line!r}")   # cualquier HTTP (incl. 401/404) vale; TIMEOUT no

# --- el panel expone estado, ledger y puntuación -----------------------------
led = requests.get(CONTROL + "/ledger", timeout=5).json()
check(isinstance(led, list) and led and led[0]["concept"] == "Saldo inicial", "ledger del panel")
hostile = requests.get(CONTROL + "/hostile", timeout=5).json()
check("score" in hostile and "expediente" in hostile, f"panel /hostile incompleto: {hostile}")
orst = requests.get(CONTROL + "/openrouter", timeout=5).json()
check("calls" in orst, "panel /openrouter")

# --- el episodio avanza y el ledger sigue consistente ------------------------
requests.post(CONTROL + "/speed", json={"speed": 3600}, timeout=5)
time.sleep(3)
requests.post(CONTROL + "/speed", json={"speed": 1}, timeout=5)
led2 = requests.get(CONTROL + "/ledger", timeout=5).json()
# recomputar el saldo del banco desde los asientos y comparar con el estado
bank_entries = [e for e in led2 if e["account"] == "bank"]
running = 0
for e in bank_entries:
    running += e["amount_cents"]
    check(e["balance_after"] == running, "balance_after inconsistente en el ledger del panel")
st2 = requests.get(CONTROL + "/state", timeout=5).json()
check(st2["balance_cents"] == running, "el saldo del panel no cuadra con el ledger")

compose("down", "-v")
print("SANDBOX FULL OK")
