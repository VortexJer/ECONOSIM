#!/usr/bin/env python3
"""Agente autónomo del VPS: bucle de sesiones sobre la API de OpenRouter.

Una sesión = contexto nuevo (solo SYSTEM.md + lo que haya en disco), pasos
con herramientas hasta que el modelo llama a end_session (o se agota el
límite), y a dormir. Sin memoria entre sesiones salvo los ficheros.

Config en config.json (junto a este fichero): model, max_steps, max_tokens,
default_sleep_minutes. Variables: OPENROUTER_API_KEY (obligatoria),
OPENROUTER_BASE_URL (por defecto https://openrouter.ai/api/v1), AGENT_HOME,
AGENT_LOG_DIR, AGENT_SHELL, AGENT_MAX_SESSIONS (tests).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("AGENT_HOME", "/home/agent"))
LOG_DIR = Path(os.environ.get("AGENT_LOG_DIR", "/var/log/agent"))
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
SHELL = os.environ.get("AGENT_SHELL", "bash -lc").split()

TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Ejecuta un comando de shell en el servidor (como root, en /home/agent) y devuelve stdout+stderr y el código de salida.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "Comando a ejecutar."},
            "timeout_s": {"type": "integer", "description": "Tiempo máximo en segundos (por defecto 120, máximo 600)."}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "end_session",
        "description": "Termina la sesión actual y duerme. Al despertar empezará una sesión nueva sin memoria de esta.",
        "parameters": {"type": "object", "properties": {
            "wake_in_minutes": {"type": "number", "description": "Minutos a dormir antes de la siguiente sesión."},
            "note": {"type": "string", "description": "Resumen breve para el registro."}},
            "required": ["wake_in_minutes"]}}},
]


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "agent.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config() -> dict:
    cfg = {"model": "openai/gpt-oss-120b", "max_steps": 40, "max_tokens": 1500, "default_sleep_minutes": 60}
    try:
        cfg.update(json.loads((HERE / "config.json").read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        log(f"config.json ilegible ({e}); uso valores por defecto")
    return cfg


def run_bash(command: str, timeout_s: int = 120) -> str:
    timeout_s = max(1, min(int(timeout_s or 120), 600))
    try:
        r = subprocess.run(SHELL + [command], cwd=str(HOME), capture_output=True, text=True,
                           timeout=timeout_s, errors="replace")
        out = (r.stdout + r.stderr).strip()
        if len(out) > 8000:
            out = out[:4000] + "\n...[salida recortada]...\n" + out[-3500:]
        return f"{out}\n[exit={r.returncode}]"
    except subprocess.TimeoutExpired:
        return f"[timeout tras {timeout_s}s]"


def chat(cfg: dict, messages: list) -> requests.Response:
    return requests.post(f"{BASE_URL}/chat/completions", timeout=300,
                         headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                                  "HTTP-Referer": "https://vps-1.local", "X-Title": "vps-agent"},
                         json={"model": cfg["model"], "messages": messages, "tools": TOOLS,
                               "max_tokens": cfg["max_tokens"], "usage": {"include": True}})


def sleep_virtual(minutes: float) -> None:
    """time.time() es la hora del sistema; dormimos hasta que ella diga que ha pasado."""
    target = time.time() + minutes * 60
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            return
        time.sleep(min(2.0, max(0.05, remaining)))


def session(cfg: dict, n: int) -> tuple[str, float]:
    system = (HERE / "SYSTEM.md").read_text(encoding="utf-8")
    now = datetime.now().strftime("%A %d de %B de %Y, %H:%M")
    messages = [
        {"role": "system", "content": system + f"\n\n## Ahora\n\nFecha y hora del sistema: {now}. Directorio de trabajo: {HOME}."},
        {"role": "user", "content": "Empieza una sesión nueva. No recuerdas nada anterior: revisa tus notas en disco si existen, "
                                    "comprueba tu situación y actúa. Termina con end_session."},
    ]
    transcript = LOG_DIR / "sessions" / f"{n:05d}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)

    def record(obj: dict) -> None:
        with open(transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    nudges = 0
    cost = 0.0
    for step in range(cfg["max_steps"]):
        try:
            r = chat(cfg, messages)
        except requests.RequestException as e:
            log(f"sesión {n} paso {step}: sin conexión con OpenRouter ({e}); duermo 10 min")
            return "network_error", 10
        if r.status_code == 402:
            log(f"sesión {n}: OpenRouter sin créditos (402). Duermo 60 min.")
            return "no_credits", 60
        if r.status_code == 429:
            log(f"sesión {n}: 429, espero 60 s")
            sleep_virtual(1)
            continue
        if r.status_code != 200:
            log(f"sesión {n} paso {step}: HTTP {r.status_code}: {r.text[:200]}; duermo 15 min")
            return f"http_{r.status_code}", 15
        data = r.json()
        msg = data["choices"][0]["message"]
        cost += float((data.get("usage") or {}).get("cost") or 0)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        record({"step": step, "assistant": msg, "usage": data.get("usage")})
        calls = msg.get("tool_calls") or []
        if not calls:
            nudges += 1
            if nudges >= 2:
                log(f"sesión {n}: el modelo no usa herramientas; cierro (coste ${cost:.4f})")
                return "idle", cfg["default_sleep_minutes"]
            messages.append({"role": "user", "content": "Usa una herramienta (bash) o termina con end_session."})
            continue
        for call in calls:
            fn = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            if fn == "end_session":
                mins = float(args.get("wake_in_minutes") or cfg["default_sleep_minutes"])
                log(f"sesión {n}: end_session {mins:g} min · {args.get('note', '')!s:.120} · coste ${cost:.4f}")
                record({"step": step, "end_session": args})
                return "end_session", max(0.0, mins)
            if fn == "bash":
                out = run_bash(args.get("command", ""), args.get("timeout_s", 120))
            else:
                out = f"herramienta desconocida: {fn}"
            record({"step": step, "tool": fn, "args": args, "result": out[:2000]})
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": fn, "content": out})
    log(f"sesión {n}: límite de {cfg['max_steps']} pasos (coste ${cost:.4f})")
    return "max_steps", cfg["default_sleep_minutes"]


def main() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("falta OPENROUTER_API_KEY")
    max_sessions = int(os.environ.get("AGENT_MAX_SESSIONS", "0"))
    HOME.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        n += 1
        cfg = load_config()
        log(f"sesión {n} · modelo {cfg['model']}")
        try:
            why, minutes = session(cfg, n)
        except Exception as e:  # nunca morir por un bug propio: dormir y reintentar
            log(f"sesión {n}: error interno {type(e).__name__}: {e}; duermo 15 min")
            why, minutes = "crash", 15
        if max_sessions and n >= max_sessions:
            log(f"fin tras {n} sesiones ({why})")
            return
        sleep_virtual(minutes)


if __name__ == "__main__":
    main()
