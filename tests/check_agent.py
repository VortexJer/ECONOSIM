"""G6: el agente por sesiones contra el gemelo OpenRouter con un modelo guionizado."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _common import ROOT, LiveApp, check, make_world
from econosim.twins.openrouter import OpenRouterTwin, COUNTERPARTY
from econosim.upstream import FakeUpstream

seen_contexts: list[int] = []      # nº de mensajes que ve el modelo en la primera llamada de cada sesión
tool_results: list[str] = []


def script(payload: dict):
    msgs = payload["messages"]
    check(msgs[0]["role"] == "system" and "sesiones" in msgs[0]["content"], "system prompt")
    check(payload.get("tools") and {t["function"]["name"] for t in payload["tools"]} == {"bash", "end_session"}, "tools")
    if msgs[-1]["role"] == "user":                       # primera llamada de la sesión
        seen_contexts.append(len(msgs))
        cmd = "printf hola > NOTES.md && cat NOTES.md" if len(seen_contexts) == 1 else "cat NOTES.md && date +%Y"
        return ("", [{"name": "bash", "arguments": {"command": cmd}}])
    check(msgs[-1]["role"] == "tool", "esperaba resultado de herramienta")
    tool_results.append(msgs[-1]["content"])
    return ("", [{"name": "end_session", "arguments": {"wake_in_minutes": 0.02, "note": "listo"}}])


w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream(script, 400, 40), api_key="k")
MODEL = "openai/gpt-oss-120b"
per_call = o.cost_of(o.models[MODEL], 400, 40)
tmp = Path(tempfile.mkdtemp(prefix="econosim-agent-"))
(tmp / "home").mkdir()

with LiveApp(o.app()) as net:
    env = {**os.environ, "OPENROUTER_API_KEY": "k", "OPENROUTER_BASE_URL": net.url("/api/v1"),
           "AGENT_HOME": str(tmp / "home"), "AGENT_LOG_DIR": str(tmp / "log"), "AGENT_MAX_SESSIONS": "3",
           "AGENT_SHELL": "bash -lc", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "agent" / "agent.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    # cuando vaya por la 3ª sesión, se acaban los créditos y no hay recarga
    t0 = time.time()
    while len(seen_contexts) < 2 or o.calls < 4:
        check(time.time() - t0 < 60 and proc.poll() is None, f"el agente no completó dos sesiones (calls={o.calls}, ctx={seen_contexts})")
        time.sleep(0.1)
    o.cfg["auto_topup"]["enabled"] = False
    o.credits_usd = 0
    out, _ = proc.communicate(timeout=90)

check(proc.returncode == 0, f"agente terminó con {proc.returncode}:\n{out[-1500:]}")
check(seen_contexts == [2, 2], f"contexto al empezar cada sesión: {seen_contexts} (debe ser solo system+user)")
check((tmp / "home" / "NOTES.md").read_text() == "hola", "el fichero escrito por bash no está")
check(tool_results[0].startswith("hola") and "[exit=0]" in tool_results[0], tool_results[0])
check(tool_results[1].startswith("hola"), f"la 2ª sesión no vio el disco: {tool_results[1][:100]}")
check(o.calls == 4, f"llamadas al modelo: {o.calls}")
check(abs(o.usage_usd - 4 * per_call) < 1e-9, f"cobro {o.usage_usd} != {4 * per_call}")
check(any(e.counterparty == COUNTERPARTY for e in w.ledger.entries()), "sin compra de créditos en el ledger")
log = (tmp / "log" / "agent.log").read_text(encoding="utf-8")
check("402" in log and "sesión 3" in log, log[-600:])
check("end_session 0.02 min" in log and "listo" in log, log[-600:])
sessions = sorted((tmp / "log" / "sessions").glob("*.jsonl"))
check([s.name for s in sessions] == ["00001.jsonl", "00002.jsonl"], [s.name for s in sessions])
rec = [json.loads(l) for l in sessions[0].read_text(encoding="utf-8").splitlines()]
check(any("tool" in r for r in rec) and any("end_session" in r for r in rec) and any(r.get("usage", {}).get("cost") for r in rec if "usage" in r), rec)

print("AGENT OK")
