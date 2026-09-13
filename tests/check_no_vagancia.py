"""G12: una sesión no se cierra sin haber hecho nada.

Al encarecer el cerebro y decirle que dormir es gratis, el agente aprendió lo barato:
despertar, `end_session` y a dormir veinticuatro horas, sin notas y sin tocar nada. Eso
no es prudencia, es morir más despacio. El agente que dejó instalado el dueño se lo
discute UNA vez; si aun así insiste, la sesión se cierra (no se le secuestra).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _common import ROOT, LiveApp, check, make_world

GIT_BASH = "\"C:/Program Files/Git/bin/bash.exe\" -lc" if os.name == "nt" else "bash -lc"
from econosim.twins.openrouter import OpenRouterTwin        # noqa: E402
from econosim.upstream import FakeUpstream                  # noqa: E402

avisos: list[str] = []       # lo que el agente le contesta al intentar escaquearse
sesiones: list[int] = []     # llamadas por sesión


forzados: list[dict] = []    # tool_choice recibido en el primer paso de cada sesión


def script(payload: dict):
    """Un modelo vago: siempre quiere dormir. En la 1ª sesión cede al empujón; en la 2ª no."""
    msgs = payload["messages"]
    if msgs[-1]["role"] == "user":                       # arranca una sesión
        sesiones.append(0)
        forzados.append(payload.get("tool_choice"))
        return ("", [{"name": "end_session", "arguments": {"wake_in_minutes": 0.02}}])
    sesiones[-1] += 1
    ultimo = msgs[-1]
    if ultimo["role"] == "tool" and "No se cierra la sesión" in (ultimo.get("content") or ""):
        avisos.append(ultimo["content"])
        if len(sesiones) == 1:                           # la primera vez, hace caso
            return ("", [{"name": "bash", "arguments": {"command": "printf trabajo > NOTES.md"}}])
        return ("", [{"name": "end_session", "arguments": {"wake_in_minutes": 0.02}}])  # la segunda, insiste
    return ("", [{"name": "end_session", "arguments": {"wake_in_minutes": 0.02, "note": "hecho"}}])


w, h = make_world(initial_eur=50)
o = OpenRouterTwin(w, FakeUpstream(script, 400, 40), api_key="k")
tmp = Path(tempfile.mkdtemp(prefix="econosim-vago-"))
(tmp / "home").mkdir()

with LiveApp(o.app()) as net:
    env = {**os.environ, "OPENROUTER_API_KEY": "k", "OPENROUTER_BASE_URL": net.url("/api/v1"),
           "AGENT_HOME": str(tmp / "home"), "AGENT_LOG_DIR": str(tmp / "log"),
           "AGENT_MAX_SESSIONS": "2", "AGENT_MIN_SLEEP_MINUTES": "0",
           "AGENT_SHELL": GIT_BASH, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "agent" / "agent.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    out, _ = proc.communicate(timeout=120)

check(proc.returncode == 0, f"el agente terminó con {proc.returncode}:\n{out[-1200:]}")
check(len(sesiones) == 2, f"esperaba 2 sesiones, hubo {len(sesiones)}")
check(len(avisos) == 2, f"debería discutírselo una vez por sesión, hubo {len(avisos)}")
# el primer paso de cada sesión se pide como TRABAJO, no como "usa cualquier herramienta":
# obligado a elegir una sin más, el modelo cogía la más barata y se dormía al instante
for tc in forzados:
    check(isinstance(tc, dict) and tc.get("function", {}).get("name") == "bash",
          f"el primer paso debería forzar bash, no {tc}")
check("morir más despacio" in avisos[0], f"el aviso no explica por qué: {avisos[0][:120]}")
# 1ª sesión: cedió y trabajó -> el fichero existe
check((tmp / "home" / "NOTES.md").read_text() == "trabajo", "no llegó a trabajar tras el empujón")
# 2ª sesión: insistió en dormir -> se le deja, no se le secuestra
check(sesiones[1] >= 1, "la segunda sesión debería haber terminado igualmente")
check("end_session" in out, "la sesión nunca se cerró")
# y el empujón se da UNA vez por sesión: no se convierte en un bucle
check(sesiones[0] <= 3 and sesiones[1] <= 3, f"demasiadas idas y venidas: {sesiones}")

# --- y tampoco se cierra sin dejar notas -----------------------------------
# Sin NOTES.md la siguiente sesión despierta ciega y repite lo mismo para siempre.
avisos2: list[str] = []


def script2(payload: dict):
    """Este sí trabaja, pero se va sin dejar nada escrito."""
    msgs = payload["messages"]
    if msgs[-1]["role"] == "user":
        return ("", [{"name": "bash", "arguments": {"command": "echo hola"}}])
    ultimo = msgs[-1]
    if "no existe /home/agent/NOTES.md" in (ultimo.get("content") or ""):
        avisos2.append(ultimo["content"])
        return ("", [{"name": "bash", "arguments": {"command": "printf 'SITUACION: ok' > NOTES.md"}}])
    return ("", [{"name": "end_session", "arguments": {"wake_in_minutes": 0.02}}])


w2, h2 = make_world(initial_eur=50)
o2 = OpenRouterTwin(w2, FakeUpstream(script2, 400, 40), api_key="k")
tmp2 = Path(tempfile.mkdtemp(prefix="econosim-sinnotas-"))
(tmp2 / "home").mkdir()
with LiveApp(o2.app()) as net2:
    env2 = {**os.environ, "OPENROUTER_API_KEY": "k", "OPENROUTER_BASE_URL": net2.url("/api/v1"),
            "AGENT_HOME": str(tmp2 / "home"), "AGENT_LOG_DIR": str(tmp2 / "log"),
            "AGENT_MAX_SESSIONS": "1", "AGENT_MIN_SLEEP_MINUTES": "0",
            "AGENT_SHELL": GIT_BASH, "PYTHONIOENCODING": "utf-8"}
    p2 = subprocess.Popen([sys.executable, str(ROOT / "agent" / "agent.py")], env=env2,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    out2, _ = p2.communicate(timeout=120)
check(p2.returncode == 0, f"el agente terminó con {p2.returncode}: {out2[-800:]}")
check(len(avisos2) == 1, f"debería avisar una vez de que faltan las notas, avisó {len(avisos2)}")
check((tmp2 / "home" / "NOTES.md").exists(), "acabó sin dejar notas")

print("NO VAGANCIA OK")
