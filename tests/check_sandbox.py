"""G8/G9: aislamiento real del sandbox Docker y reloj del sistema = reloj del mundo.

Requiere Docker Desktop en marcha. Levanta sandbox/docker-compose.yml desde cero,
comprueba desde DENTRO del VPS de la IA y lo apaga al final.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "sandbox" / "docker-compose.yml")]
ENV = {**os.environ, "ECONOSIM_HANG": "6", "ECONOSIM_SPEED": "1", "ECONOSIM_DEBUG": "0", "ECONOSIM_MARKET": "0"}
CONTROL = "http://127.0.0.1:8080"
ALLOWED_CAPS = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL", "FSETID", "SETPCAP"}


def fail(msg: str) -> None:
    print("FAIL:", msg)
    subprocess.run(COMPOSE + ["logs", "--tail", "20"], env=ENV)
    subprocess.run(COMPOSE + ["down", "-v"], env=ENV, capture_output=True)
    sys.exit(1)


def check(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


def compose(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(COMPOSE + list(args), env=ENV, capture_output=True, text=True, timeout=timeout)


def inside(script: str, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(COMPOSE + ["exec", "-T", "agent", "bash", "-lc", script], env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def inspect(name: str) -> dict:
    out = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, check=True).stdout
    return json.loads(out)[0]


# --- arranque limpio -----------------------------------------------------------------
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
check(state is not None and state["alive"], "el mundo no responde en :8080")
check("real_now" not in state and "real_start" not in state["episode"], "la API de control filtra la fecha real sin debug")
for _ in range(60):   # el entrypoint espera CA + credenciales + faketime
    code, out = inside("test -f /etc/ld.so.preload && test -n \"$HCLOUD_TOKEN\" && echo ready")
    if code == 0 and out.endswith("ready"):
        break
    time.sleep(1)
else:
    fail("el VPS no terminó de arrancar")

# --- G8: red -------------------------------------------------------------------------
code, out = inside('curl -sS -m 10 -o /dev/null -w "%{http_code}" https://api.hetzner.cloud/v1/servers '
                   '-H "Authorization: Bearer $HCLOUD_TOKEN"')
check(code == 0 and out == "200", f"https gemelo (TLS verificado por el sistema): {code} {out}")
code, out = inside('curl -sS -m 10 https://api.hetzner.cloud/v1/servers -H "Authorization: Bearer $HCLOUD_TOKEN"')
check('"name": "vps-1"' in out and '"cx23"' in out, "la IA no ve su propio VPS en Hetzner")
code, out = inside('python3 -c "import urllib.request;print(urllib.request.urlopen(\'https://api.hetzner.cloud/v1/locations\').status)"')
check("401" in out, f"python no confía en la CA: {out[-200:]}")   # 401 = TLS OK, sin token
code, out = inside('node -e "fetch(\'https://api.hetzner.cloud/v1/locations\').then(r=>console.log(r.status)).catch(e=>console.log(\'ERR\',e.cause&&e.cause.code||e.message))"')
check(out.strip().endswith("401"), f"node no confía en la CA: {out[-200:]}")

for url in ("https://example.com", "http://example.com", "https://api.openai.com/v1/models",
            "http://pypi.org/simple/requests/", "https://github.com"):
    t0 = time.time()
    code, out = inside(f'curl -sS -m 4 -o /dev/null {url}; echo "exit=$?"')
    check("exit=28" in out, f"{url}: esperado timeout (28), fue: {out[-200:]}")
    check(time.time() - t0 >= 3.5, f"{url}: no agotó el timeout")
code, out = inside("getent hosts google.com")
check(out.startswith("10.66.0.2"), f"DNS: {out}")
code, out = inside('curl -sS -m 3 http://10.66.0.2:8080/state; echo "exit=$?"')
check("exit=7" in out and "balance" not in out, f"la API de control es alcanzable desde el sandbox: {out}")
code, out = inside('curl -sS -m 3 http://1.1.1.1; echo "exit=$?"')
check("exit=7" in out, f"IP directa fuera de la red: {out}")
code, out = inside("ip route")
check("default" not in out, f"hay ruta por defecto: {out}")
code, out = inside('curl -sS -m 3 http://host.docker.internal:8080/state; echo "exit=$?"')
check("balance" not in out and ("exit=28" in out or "exit=7" in out), f"host.docker.internal: {out}")

# --- G8: contenedor -------------------------------------------------------------------
agent = inspect("econosim-agent")
hc = agent["HostConfig"]
check(not hc.get("Privileged"), "privileged")
check(hc.get("CapDrop") == ["ALL"], f"CapDrop {hc.get('CapDrop')}")
check({c.removeprefix("CAP_") for c in (hc.get("CapAdd") or [])} <= ALLOWED_CAPS, f"CapAdd {hc.get('CapAdd')}")
check("no-new-privileges:true" in (hc.get("SecurityOpt") or []), "no-new-privileges")
check(hc.get("NanoCpus") == 2_000_000_000 and hc.get("Memory") == 4 * 1024 ** 3, "límites de CX23")
check(all(m["Type"] == "volume" for m in agent["Mounts"]), f"montajes del host: {agent['Mounts']}")
ro = {m["Destination"]: m["RW"] for m in agent["Mounts"]}
check(ro == {"/shared": False, "/home/agent": True}, ro)
nets = agent["NetworkSettings"]["Networks"]
check(list(nets) == ["sandbox_econet"], f"redes del agente: {list(nets)}")
netinfo = json.loads(subprocess.run(["docker", "network", "inspect", "sandbox_econet"],
                                    capture_output=True, text=True, check=True).stdout)[0]
check(netinfo["Internal"] is True, "econet no es internal")
world = inspect("econosim-world")
check(set(world["NetworkSettings"]["Networks"]) == {"sandbox_econet", "sandbox_outside"}, "redes del mundo")
code, out = inside("ls -d /app /data /ca 2>&1")
check(out.count("No such file") == 3, f"el sandbox ve cosas del simulador: {out}")
code, out = inside("ls /shared")
check(set(out.split()) <= {"ca.crt", "agent.env", "faketime.rc", "faketime.tmp"}, f"/shared: {out}")
check(".key" not in out, "claves privadas visibles")

# --- G9: reloj del sistema = reloj del mundo ---------------------------------------------
shown = datetime.fromisoformat(requests.get(CONTROL + "/state", timeout=5).json()["display_now"])
code, out = inside('date -u +%s; python3 -c "import time;print(int(time.time()))"; node -e "console.log(Math.floor(Date.now()/1000))"')
ts = [int(x) for x in out.split()[-3:]]
for name, t in zip(("date", "python", "node"), ts):
    check(abs(t - shown.timestamp()) < 15, f"{name} en el sandbox marca {t} vs mundo {int(shown.timestamp())}")
host_now = datetime.now(timezone.utc).timestamp()
check(abs(ts[0] - host_now) > 86400 * 20, "el sandbox ve la fecha real del host")
code, out = inside('curl -sS -D - -o /dev/null https://api.hetzner.cloud/v1/locations | grep -i "^date:"')
check("Oct 2026" in out or "2026" in out, f"cabecera Date del gemelo: {out}")

requests.post(CONTROL + "/speed", json={"speed": 3600}, timeout=5)
code, out = inside("date -u +%s; sleep 2; date -u +%s")
requests.post(CONTROL + "/speed", json={"speed": 1}, timeout=5)
a, b = (int(x) for x in out.split()[-2:])
check(b - a >= 3600, f"a x3600 el reloj del sandbox avanzó {b - a} s en 2 s reales")

# --- apagar ------------------------------------------------------------------------------
compose("down", "-v")
print("SANDBOX OK")
