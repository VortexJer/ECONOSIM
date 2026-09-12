"""Juega N vidas completas en el sandbox y cosecha las trayectorias + su puntuación.

Cada vida: mundo + agente en Docker con el cerebro LOCAL (qwen3b), reloj fiel (x1 al
pensar, comprimido al dormir) y un horizonte (--duration). Termina por MUERTE o por
llegar al horizonte; dormir no termina nada.

Al acabar guarda en training/data/episodes/<semilla>/:
    sessions/*.jsonl   lo que pensó e hizo en cada sesión
    outcome.json       puntuación, saldo, causa de fin

Arranca el mundo ANTES que el agente y espera a que el control responda: si no, el agente
lee un agent.env viejo y se queda con credenciales caducadas (401 "User not found").
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SANDBOX = ROOT / "sandbox"
DATA = HERE / "data"
LOGS = DATA / "agent-logs"
EPISODES = DATA / "episodes"
CONTROL = "http://127.0.0.1:8080"

COMPOSE = ["docker", "compose", "--project-directory", str(SANDBOX),
           "-f", str(SANDBOX / "docker-compose.yml"),
           "-f", str(SANDBOX / "docker-compose.local.yml")]


def dc(*args, env=None, quiet=True):
    return subprocess.run(COMPOSE + list(args), env=env, capture_output=quiet, text=True)


def dashboard():
    try:
        with urlopen(CONTROL + "/dashboard", timeout=5) as r:
            return json.loads(r.read())
    except (URLError, OSError, ValueError):
        return None


def wait_control(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if dashboard() is not None:
            return True
        time.sleep(2)
    return False


def play(seed: str, duration: str, timeout_s: float, env_base: dict) -> dict | None:
    print(f"\n=== vida {seed} (horizonte {duration}) ===", flush=True)
    dc("down", "-v")                                  # volúmenes limpios: credenciales frescas
    if LOGS.exists():
        shutil.rmtree(LOGS, ignore_errors=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    env = dict(env_base)
    env["ECONOSIM_EPISODE_SEED"] = seed
    env["ECONOSIM_SIM_DURATION"] = duration

    dc("up", "-d", "world", env=env)                  # 1) el mundo primero
    if not wait_control():
        print("  el control no respondió; abandono esta vida")
        dc("down", "-v"); return None
    dc("up", "-d", "agent", env=env)                  # 2) el agente, ya con agent.env escrito

    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        d = dashboard()
        if d is not None:
            last = d
            if d.get("ended"):
                break
        time.sleep(5)
    if last is None:
        dc("down", "-v"); return None

    outcome = {"seed": seed, "score": last.get("score"), "balance_cents": last.get("balance_cents"),
               "equity_cents": last.get("equity_cents"), "alive": last.get("alive"),
               "ended": last.get("ended"), "end_cause": last.get("end_cause"),
               "display_now": last.get("display_now"), "real_seconds": round(time.time() - t0, 1)}

    dest = EPISODES / seed
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    (dest / "sessions").mkdir(parents=True, exist_ok=True)
    src = LOGS / "sessions"
    n = 0
    if src.exists():
        for f in sorted(src.glob("*.jsonl")):
            shutil.copy2(f, dest / "sessions" / f.name)
            n += 1
    (dest / "outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  fin: {outcome['end_cause'] or 'timeout'} · puntuación {outcome['score']} · "
          f"saldo {(outcome['balance_cents'] or 0)/100:.2f} EUR · {n} sesiones", flush=True)
    dc("down", "-v")
    return outcome


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="cuántas vidas jugar")
    ap.add_argument("--duration", default="7d", help="horizonte de cada vida: 12h, 1d, 7d, 1mo, 1y")
    ap.add_argument("--prefix", default="ep", help="prefijo de las semillas")
    ap.add_argument("--timeout", type=float, default=1800, help="segundos reales máx. por vida")
    a = ap.parse_args()

    import os
    env_base = dict(os.environ)
    EPISODES.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for i in range(a.n):
            r = play(f"{a.prefix}-{i:03d}", a.duration, a.timeout, env_base)
            if r:
                results.append(r)
    finally:
        dc("down", "-v")
    if results:
        sc = [r["score"] for r in results if r.get("score") is not None]
        print(f"\n{len(results)} vidas jugadas · puntuaciones: {sc}")
    print(f"datos en {EPISODES}")


if __name__ == "__main__":
    main()
