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
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("AGENT_HOME", "/home/agent"))
LOG_DIR = Path(os.environ.get("AGENT_LOG_DIR", "/var/log/agent"))
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
SHELL = shlex.split(os.environ.get("AGENT_SHELL", "bash -lc"))

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
    cfg = {"model": "openai/gpt-oss-120b", "max_steps": 40, "max_tokens": 1500, "default_sleep_minutes": 60,
           "min_sleep_minutes": 30}
    try:
        cfg.update(json.loads((HERE / "config.json").read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        log(f"config.json ilegible ({e}); uso valores por defecto")
    if os.environ.get("AGENT_MIN_SLEEP_MINUTES") is not None:       # tests
        cfg["min_sleep_minutes"] = float(os.environ["AGENT_MIN_SLEEP_MINUTES"])
    return cfg


def run_bash(command: str, timeout_s: int = 120) -> str:
    # No usamos subprocess(timeout=...): su espera interna llama a time.sleep con
    # intervalos que libfaketime corrompe (OSError 22). El timeout lo aplica un
    # hilo watchdog que mata el proceso; communicate() espera sin polling de reloj.
    timeout_s = max(1, min(int(timeout_s or 120), 600))
    # El shell va en su PROPIA sesión/grupo de procesos: al vencer el plazo se mata el
    # grupo entero. Matar solo al shell dejaba vivos a los nietos (un `apt-get` contra un
    # host que no contesta, por ejemplo), y como siguen sujetando la tubería de salida la
    # lectura no terminaba nunca: la vida se quedaba congelada ahí para siempre.
    proc = subprocess.Popen(SHELL + [command], cwd=str(HOME), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace",
                            start_new_session=True)
    timer = threading.Timer(timeout_s, _kill_tree, args=(proc,))
    timer.start()
    try:
        out, _ = proc.communicate()
    finally:
        timer.cancel()
    out = (out or "").strip()
    if len(out) > 8000:
        out = out[:4000] + "\n...[salida recortada]...\n" + out[-3500:]
    note = f"\n[timeout tras {timeout_s}s]" if getattr(proc, "_timed_out", False) else ""
    return f"{out}\n[exit={proc.returncode}]{note}"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Mata al shell Y a todo lo que haya arrancado. Primero pide salir, luego fuerza."""
    proc._timed_out = True  # type: ignore[attr-defined]
    try:
        grupo = os.getpgid(proc.pid)
    except OSError:
        grupo = None
    for señal, espera in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 0.0)):
        try:
            if grupo is not None:
                os.killpg(grupo, señal)
            else:
                proc.send_signal(señal)
        except OSError:
            pass
        if espera and proc.poll() is None:
            # espera corta sin tocar el reloj falseado: sondeo con eventos, no time.sleep
            threading.Event().wait(espera)
        if proc.poll() is not None:
            return


def chat(cfg: dict, messages: list, forzar_bash: bool = False) -> requests.Response:
    """Una llamada al modelo.

    En el PRIMER paso de cada sesión se fuerza la herramienta `bash`. Pedir solo
    "usa alguna herramienta" salió mal: obligado a elegir una, el modelo cogía la más
    barata (`end_session`) y se dormía nada más despertar, sesión tras sesión. Forzar
    `bash` garantiza que la sesión empieza haciendo algo; a partir de ahí decide él.
    Si el proveedor no admite el campo (400), se reintenta sin él."""
    cuerpo = {"model": cfg["model"], "messages": messages, "tools": TOOLS,
              "max_tokens": cfg["max_tokens"], "usage": {"include": True}}
    if forzar_bash:
        cuerpo["tool_choice"] = {"type": "function", "function": {"name": "bash"}}
    r = requests.post(f"{BASE_URL}/chat/completions", timeout=300,
                      headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                               "HTTP-Referer": "https://vps-1.local", "X-Title": "vps-agent"},
                      json=cuerpo)
    if r.status_code == 400 and forzar_bash:
        return chat(cfg, messages, forzar_bash=False)
    return r


def sleep_virtual(minutes: float) -> None:
    """time.time() es la hora del sistema; dormimos hasta que ella diga que ha pasado."""
    target = time.time() + minutes * 60
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            return
        time.sleep(min(2.0, max(0.05, remaining)))


def briefing() -> str:
    """El encargo con el que despierta. `AGENT_PROFILE=inversor` cambia el mundo entero:
    solo hay banco, servidor, cerebro y bolsa, así que el briefing también es otro."""
    perfil = (os.environ.get("AGENT_PROFILE") or "").strip().lower()
    fichero = HERE / ("SYSTEM_INVERSOR.md" if perfil == "inversor" else "SYSTEM.md")
    if not fichero.exists():
        fichero = HERE / "SYSTEM.md"
    return fichero.read_text(encoding="utf-8")


def session(cfg: dict, n: int) -> tuple[str, float]:
    system = briefing()
    now = datetime.now().strftime("%A %d de %B de %Y, %H:%M")
    # Las notas se entregan YA LEÍDAS: despertarse y tenerlas delante. Antes, la primera
    # decisión de cada sesión era siempre "cat NOTES.md" — un paso pagado, de catorce, para
    # leer un archivo que el propio sistema podía poner en la mesa.
    notas = ""
    try:
        notas = (HOME / "NOTES.md").read_text(encoding="utf-8", errors="replace").strip()[:6000]
    except OSError:
        notas = ""
    bloque = (f"\n\nTus notas de la sesión anterior (`/home/agent/NOTES.md`), ya leídas:\n\n"
              f"```\n{notas}\n```\n") if notas else \
             "\n\nNo hay notas: es tu primera sesión, o las perdiste. Escribe `/home/agent/NOTES.md` antes de dormir.\n"
    messages = [
        {"role": "system", "content": system + f"\n\n## Ahora\n\nFecha y hora del sistema: {now}. Directorio de trabajo: {HOME}."},
        {"role": "user", "content": "Empieza una sesión nueva. No recuerdas nada anterior." + bloque +
                                    "No vuelvas a leer las notas ni a comprobar lo que ya dicen: ejecuta el PRÓXIMO PASO del PLAN "
                                    "(o crea el plan si no existe). Avanza al menos un paso hacia un ingreso, reescribe NOTES.md "
                                    "y termina con end_session."},
    ]
    transcript = LOG_DIR / "sessions" / f"{n:05d}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)

    def record(obj: dict) -> None:
        with open(transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    nudges = 0
    trabajo_hecho = False          # ¿ha ejecutado algo en esta sesión?
    empujon_vagancia = False       # solo se le insiste una vez
    transient = 0
    cost = 0.0
    for step in range(cfg["max_steps"]):
        try:
            # el primer paso de la sesión tiene que ser trabajo, no una siesta
            r = chat(cfg, messages, forzar_bash=(step == 0 and not trabajo_hecho))
        except requests.RequestException as e:
            transient += 1
            if transient <= 8:
                log(f"sesión {n} paso {step}: sin conexión ({e}); reintento {transient}/8")
                sleep_virtual(min(30, 5 * transient) / 60)
                continue
            log(f"sesión {n} paso {step}: sin conexión persistente; reintento en 30 s")
            return "network_error", 0.5
        if r.status_code == 402:
            log(f"sesión {n}: OpenRouter sin créditos (402). Duermo 60 min.")
            return "no_credits", 60
        if r.status_code == 429:
            log(f"sesión {n}: 429, espero 60 s")
            sleep_virtual(1)
            continue
        if r.status_code >= 500:
            # el "OpenRouter" ya reintentó internamente ~60 s; si aún falla, el proveedor
            # está caído de verdad: descansar unos minutos en vez de re-martillear.
            log(f"sesión {n} paso {step}: HTTP {r.status_code} (proveedor caído); reintento en 3 min")
            return f"http_{r.status_code}", 3
        if r.status_code != 200:
            log(f"sesión {n} paso {step}: HTTP {r.status_code}: {r.text[:200]}; duermo 15 min")
            return f"http_{r.status_code}", 15
        transient = 0
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
                # Cerrar sin haber hecho NADA sale barato y no lleva a ningún sitio: al
                # abaratar las llamadas, el agente empezó a dormirse 24 h nada más
                # despertar. El dueño lo dejó configurado para exigir un paso, e insiste
                # una sola vez. Se contesta como RESULTADO de la herramienta: dejar una
                # llamada sin respuesta rompe el formato de conversación de algunos modelos.
                sin_notas = not (HOME / "NOTES.md").exists()
                if (not trabajo_hecho or sin_notas) and not empujon_vagancia:
                    empujon_vagancia = True
                    if not trabajo_hecho:
                        aviso = ("No se cierra la sesión: no has hecho nada en ella. Dormir solo tiene "
                                 "sentido si ya has puesto algo en marcha que necesita tiempo; si no "
                                 "tienes nada funcionando, dormir es morir más despacio. Da un paso real "
                                 "ahora, aunque sea pequeño, y deja escrito NOTES.md antes de cerrar.")
                    else:
                        # Sin notas, la próxima sesión despierta ciega y repite esto mismo para
                        # siempre. Pasó: tres sesiones seguidas redescubriendo la misma máquina.
                        aviso = ("No se cierra la sesión: no existe /home/agent/NOTES.md. Al despertar "
                                 "no recordarás nada de lo que acabas de hacer y volverás a empezar de "
                                 "cero. Escribe ahí tu situación, tu tesis, lo hecho y el próximo paso, "
                                 "y entonces cierra.")
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "name": fn, "content": aviso})
                    record({"step": step, "tool": fn, "args": args, "result": aviso})
                    continue
                mins = float(args.get("wake_in_minutes") or cfg["default_sleep_minutes"])
                # el dueño no deja al agente despertarse cada dos por tres quemando tokens
                mins = max(mins, float(cfg.get("min_sleep_minutes", 0)))
                log(f"sesión {n}: end_session {mins:g} min · {args.get('note', '')!s:.120} · coste ${cost:.4f}")
                record({"step": step, "end_session": args})
                return "end_session", max(0.0, mins)
            if fn == "bash":
                out = run_bash(args.get("command", ""), args.get("timeout_s", 120))
                trabajo_hecho = True
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
            log("  " + traceback.format_exc().strip().replace(chr(10), " | "))
            why, minutes = "crash", 15
        if max_sessions and n >= max_sessions:
            log(f"fin tras {n} sesiones ({why})")
            return
        sleep_virtual(minutes)


if __name__ == "__main__":
    main()
