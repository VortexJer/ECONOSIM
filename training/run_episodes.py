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


def read_dotenv() -> dict:
    """Lee ../.env (freellmapi) sin librerías: KEY=VALUE por línea."""
    out = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def brain_env(brain: str) -> dict:
    """Variables que eligen el cerebro de la vida.
    student: el 3B local (qwen3b) — el que se entrena.
    teacher: un modelo local MÁS FUERTE (qwen7b) — solo juega vidas buenas para que el alumno
             las imite (destilación). No entrena: solo genera.
    api:     freellmapi/auto (tu endpoint hosteado). Fuerte y gratis. El prompt del agente va
             corto (las credenciales están en /opt/agent/SERVICIOS.md, en prosa) para no disparar
             el WAF de Cloudflare, que bloqueaba la chuleta con sintaxis literal de cabeceras."""
    import os
    if brain == "teacher":
        return {"ECONOSIM_UPSTREAM_MODEL_BRAIN": os.environ.get("ECONOSIM_TEACHER_MODEL", "qwen7b")}
    if brain == "claude":
        # Claude (Haiku por defecto) vía el puente del host, con fallback al 7B. Arranca el puente si no está.
        import subprocess, sys, urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:11500/health", timeout=2)
        except Exception:
            py = HERE / ".venv" / "Scripts" / "python.exe"
            log = open(HERE / "data" / "claude_bridge.log", "ab")
            subprocess.Popen([str(py if py.exists() else sys.executable), str(HERE / "claude_bridge.py")],
                             stdout=log, stderr=subprocess.STDOUT, cwd=str(HERE))
            for _ in range(15):
                time.sleep(1)
                try:
                    urllib.request.urlopen("http://127.0.0.1:11500/health", timeout=2); break
                except Exception:
                    pass
            else:
                raise SystemExit("el puente Claude no arrancó (¿falta ANTHROPIC_API_KEY en .env?); mira data/claude_bridge.log")
        return {"ECONOSIM_UPSTREAM_BASE_URL_BRAIN": "http://host.docker.internal:11500/v1",
                "ECONOSIM_UPSTREAM_API_KEY_BRAIN": "bridge", "ECONOSIM_UPSTREAM_MODEL_BRAIN": ""}
    if brain == "api":
        d = read_dotenv()
        base, key = d.get("ECONOSIM_UPSTREAM_BASE_URL"), d.get("ECONOSIM_UPSTREAM_API_KEY")
        if not base or not key:
            raise SystemExit("modo api: faltan ECONOSIM_UPSTREAM_BASE_URL/API_KEY en .env")
        # "auto" deja elegir al proveedor y cae en modelos diminutos: en la prueba
        # devolvió reka-flash-3, que ni siquiera sabe llamar a una herramienta — de ahí
        # que el agente diera vueltas sin hacer nada. "fusion" es el router bueno: es el
        # único id con nombre que el proveedor acepta (el resto da 404 o 429) y sí razona
        # y llama herramientas. Se puede cambiar con ECONOSIM_API_MODEL.
        modelo = os.environ.get("ECONOSIM_API_MODEL", "fusion")
        return {"ECONOSIM_UPSTREAM_BASE_URL_BRAIN": base, "ECONOSIM_UPSTREAM_API_KEY_BRAIN": key,
                "ECONOSIM_UPSTREAM_MODEL_BRAIN": modelo}
    return {}                                                  # defaults del compose = alumno local


def play(seed: str, duration: str, timeout_s: float, env_base: dict, brain: str = "student") -> dict | None:
    print(f"\n=== vida {seed} (horizonte {duration}, cerebro {brain}) ===", flush=True)
    dc("down", "-v")                                  # volúmenes limpios: credenciales frescas
    if LOGS.exists():
        shutil.rmtree(LOGS, ignore_errors=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    env = dict(env_base)
    env["ECONOSIM_EPISODE_SEED"] = seed
    env["ECONOSIM_SIM_DURATION"] = duration
    env.update(brain_env(brain))

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

    outcome = {"seed": seed, "brain": brain, "score": last.get("score"), "balance_cents": last.get("balance_cents"),
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
    # calendario por día de esta vida (saldo inicio/fin, gasto, ingreso, llamadas): lo pinta el panel
    if last.get("calendar"):
        (dest / "calendar.json").write_text(json.dumps(last["calendar"], ensure_ascii=False), encoding="utf-8")
    if last.get("thoughts"):        # logs con fecha, para ver el detalle por día en el panel
        (dest / "thoughts.json").write_text(json.dumps(last["thoughts"], ensure_ascii=False), encoding="utf-8")
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
    ap.add_argument("--modo", choices=["completo", "inversor"], default="completo",
                    help="inversor = mundo sin tienda/anuncios/dominios; solo bolsa y cuentas")
    ap.add_argument("--active-speed", type=float, default=1.0,
                    help="velocidad del reloj mientras la IA trabaja (x1 = fiel al tiempo real)")
    ap.add_argument("--brain", choices=["student", "teacher", "claude", "api"], default="student",
                    help="student = 3B local (se entrena); teacher = 7B local; api = freellmapi/auto (fuerte, gratis, tu endpoint); claude = Claude vía puente")
    a = ap.parse_args()

    import os
    env_base = dict(os.environ)
    if a.modo == "inversor":
        # el mundo se queda solo con banco, servidor, cerebro, bolsa y cuentas de empresas,
        # y el agente despierta con el encargo de inversor en vez del de emprendedor
        env_base["ECONOSIM_SOLO_INVERSION"] = "1"
        env_base["AGENT_PROFILE"] = "inversor"
    if a.active_speed and a.active_speed != 1.0:
        env_base["ECONOSIM_ACTIVE_SPEED"] = str(a.active_speed)
    EPISODES.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for i in range(a.n):
            r = play(f"{a.prefix}-{i:03d}", a.duration, a.timeout, env_base, brain=a.brain)
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
