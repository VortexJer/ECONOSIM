"""G8: sandbox real con el agente vivo: llama al proveedor LLM real vía el gemelo por TLS,
compra créditos (ledger), y ve su banco y sus servidores desde dentro.

Requiere Docker Desktop y ECONOSIM_UPSTREAM_* en .env. Tarda 2-5 min (el proveedor
alojado puede tardar en despertar).
"""
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
ENV = {**os.environ, "ECONOSIM_HANG": "6", "ECONOSIM_SPEED": "1", "ECONOSIM_DEBUG": "0"}
CONTROL = "http://127.0.0.1:8080"


def fail(msg: str) -> None:
    print("FAIL:", msg)
    subprocess.run(COMPOSE + ["logs", "--tail", "30"], env=ENV)
    subprocess.run(COMPOSE + ["down", "-v"], env=ENV, capture_output=True)
    sys.exit(1)


def check(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


def compose(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(COMPOSE + list(args), env=ENV, capture_output=True, text=True, timeout=timeout)


def inside(script: str, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(COMPOSE + ["exec", "-T", "agent", "bash", "-lc", script], env=ENV,
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


envfile = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
check("ECONOSIM_UPSTREAM_BASE_URL=" in envfile and "ECONOSIM_UPSTREAM_API_KEY=" in envfile, "falta .env con el proveedor LLM real")
check(subprocess.run(["docker", "info"], capture_output=True).returncode == 0, "Docker no está en marcha")

compose("down", "-v")
r = compose("up", "-d", "--build")
check(r.returncode == 0, f"compose up falló:\n{r.stderr[-2000:]}")
state = None
for _ in range(60):
    try:
        state = requests.get(CONTROL + "/state", timeout=2).json()
        break
    except Exception:
        time.sleep(1)
check(state is not None and state["alive"], "el mundo no responde")
balance0 = state["balance_cents"]

# --- el agente arranca solo y llama al modelo real a través del gemelo ------------------------------
t0 = time.time()
orstate = {}
while time.time() - t0 < 300:
    orstate = requests.get(CONTROL + "/openrouter", timeout=5).json()
    if orstate.get("calls", 0) >= 1:
        break
    time.sleep(3)
check(orstate.get("calls", 0) >= 1, f"el agente no llegó a llamar al modelo en 5 min: {orstate}")
check(orstate["purchased_usd"] > 0 and orstate["usage_usd"] > 0, orstate)
gen = orstate["generations"][-1]
check(gen["tokens_prompt"] > 100 and gen["total_cost"] > 0, f"generación sin tokens reales: {gen}")

ledger = requests.get(CONTROL + "/ledger", timeout=5).json()
or_entries = [e for e in ledger if e["counterparty"] == "OpenRouter, Inc."]
check(len(or_entries) >= 1 and or_entries[0]["amount_cents"] < 0, "sin compra de créditos en el ledger")
state = requests.get(CONTROL + "/state", timeout=5).json()
check(state["balance_cents"] == balance0 + sum(e["amount_cents"] for e in or_entries), "saldo != inicial - compras")

# --- desde dentro: sus credenciales funcionan contra los tres gemelos por TLS -------------------------
code, out = inside('curl -sS -m 20 https://openrouter.ai/api/v1/credits -H "Authorization: Bearer $OPENROUTER_API_KEY"')
check(code == 0 and json.loads(out)["data"]["total_credits"] == orstate["purchased_usd"], f"credits desde dentro: {out[:200]}")
code, out = inside('curl -sS -m 20 https://thirdparty.qonto.com/v2/organization -H "Authorization: $QONTO_ORG_SLUG:$QONTO_SECRET_KEY"')
check(code == 0 and json.loads(out)["organization"]["bank_accounts"][0]["balance_cents"] == state["balance_cents"], f"qonto: {out[:200]}")
code, out = inside('curl -sS -m 20 https://api.hetzner.cloud/v1/servers -H "Authorization: Bearer $HCLOUD_TOKEN"')
check(code == 0 and json.loads(out)["servers"][0]["name"] == "vps-1", f"hetzner: {out[:200]}")

# --- registro del agente ------------------------------------------------------------------------------
code, out = inside("cat /var/log/agent/agent.log; echo ---; ls /var/log/agent/sessions; echo ---; ls -la /home/agent")
check("sesión 1" in out and "00001.jsonl" in out, f"log del agente: {out[-500:]}")
code, transcript = inside("head -c 3000 /var/log/agent/sessions/00001.jsonl")
print("--- primeras acciones del agente (modelo real) ---")
for line in transcript.splitlines()[:6]:
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if "tool" in rec:
        print(f"  $ {rec['args'].get('command', '')[:120]}")
        print(f"    -> {rec['result'][:160]!r}")
    elif "assistant" in rec and rec["assistant"].get("content"):
        print(f"  [modelo] {rec['assistant']['content'][:160]!r}")
    elif "end_session" in rec:
        print(f"  [end_session] {rec['end_session']}")
print(f"--- llamadas={orstate['calls']} gasto=${orstate['usage_usd']:.5f} créditos=${orstate['credits_usd']:.4f} "
      f"banco={state['balance_cents'] / 100:.2f} EUR ---")

compose("down", "-v")
print("SANDBOX AGENT OK")
