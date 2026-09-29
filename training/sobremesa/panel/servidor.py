"""Panel local del entrenamiento: http://127.0.0.1:8765  (solo biblioteca estándar, solo lectura).

/estado  -> progreso, pérdidas, exámenes, velocidad por paso, historial de la GPU y energía gastada
/log     -> las últimas líneas del log tal como se verían en una terminal
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

AQUI = Path(__file__).resolve().parent
TRAIN = AQUI.parents[2] / "training"
DATA = TRAIN / "data"
ADAPTADOR = TRAIN / "adapters" / "demos-v1"
LOG, INICIO, FIN = DATA / "train_v1.log", DATA / "train_v1.inicio", DATA / "train_v1.fin"
HIST = DATA / "panel_gpu.jsonl"            # muestras de la GPU cada 5 s: sobreviven a reiniciar el panel
BARRA = re.compile(r"(\d+)/(\d+) \[([\d:]+)<([\d:?]+),\s*([\d.]+)(s/it|it/s)")
ES_BARRA = re.compile(r"\d+%\|")
RESTO_W = 85        # estimado: CPU casi parada (~35 W) + placa, RAM, ventiladores (~30 W) + pérdidas de la fuente (~20 W)
ACCUM = 8           # sesiones por paso (--accum por defecto de train_qlora.py)

_hist: list[dict] = []
_lock = threading.Lock()


def segundos(t: str) -> int | None:
    if "?" in t:
        return None
    n = 0
    for p in t.split(":"):
        n = n * 60 + int(p)
    return n


def gpu() -> dict:
    try:
        s = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,"
                            "power.draw,fan.speed", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
        n, mu, mt, u, t, w, f = [x.strip() for x in s.split(",")]
        return {"nombre": n, "mem": int(mu), "mem_total": int(mt), "uso": int(u), "temp": int(t),
                "watios": float(w), "ventilador": int(f) if f.isdigit() else None}
    except Exception:
        return {}


def muestreador() -> None:
    if HIST.exists():
        for l in HIST.read_text(encoding="utf-8").splitlines():
            try:
                _hist.append(json.loads(l))
            except ValueError:
                pass
    while True:
        g = gpu()
        if g:
            m = {"t": round(time.time()), "temp": g["temp"], "w": g["watios"], "mem": g["mem"], "uso": g["uso"]}
            with _lock:
                _hist.append(m)
            with open(HIST, "a", encoding="utf-8") as f:
                f.write(json.dumps(m) + "\n")
        time.sleep(5)


def energia(desde: float | None) -> dict:
    """kWh desde el inicio del entrenamiento: GPU medida (integrada) + el resto del PC estimado."""
    with _lock:
        h = [m for m in _hist if desde is None or m["t"] >= desde]
    wh_gpu = wh_resto = cubierto = 0.0
    for a, b in zip(h, h[1:]):
        dt = b["t"] - a["t"]
        if 0 < dt <= 60:                               # huecos largos (panel parado): no se suman...
            wh_gpu += (a["w"] + b["w"]) / 2 * dt / 3600
            wh_resto += RESTO_W * dt / 3600
            cubierto += dt
    # ...pero se rellenan al ritmo medio medido, para que el total cuente desde el inicio del entrenamiento
    total = time.time() - desde if desde else cubierto
    k = total / cubierto if cubierto > 60 and total > cubierto else 1.0
    return {"kwh_gpu": wh_gpu * k / 1000, "kwh_total": (wh_gpu + wh_resto) * k / 1000, "resto_w": RESTO_W,
            "medido_pct": round(100 / k)}


def hist_reducido(desde: float | None, maximo: int = 720) -> list[dict]:
    with _lock:
        h = [m for m in _hist if desde is None or m["t"] >= desde - 1800]
    paso = max(1, len(h) // maximo)
    return h[::paso]


def lineas_log(txt: str) -> list[str]:
    """Como una terminal: de cada línea queda lo último que escribió tqdm tras su \\r, y las barras seguidas se funden."""
    out: list[str] = []
    for linea in txt.split("\n"):
        vis = linea.split("\r")[-1].rstrip() if "\r" in linea else linea.rstrip()
        if not vis.strip():
            partes = [p for p in linea.split("\r") if p.strip()]
            if not partes:
                continue
            vis = partes[-1].rstrip()
        if out and ES_BARRA.search(vis) and ES_BARRA.search(out[-1]) and vis.split("|")[0].strip(" 0123456789%") == \
                out[-1].split("|")[0].strip(" 0123456789%"):
            out[-1] = vis
        else:
            out.append(vis)
    return out


def estado() -> dict:
    e: dict = {"ahora": datetime.now().isoformat(timespec="seconds"), "gpu": gpu(), "perdidas": [], "examenes": [],
               "velocidades": [], "eventos": []}
    t0 = None
    if INICIO.exists():
        e["inicio"] = INICIO.read_text(encoding="utf-8-sig").strip()[:19]
        t0 = datetime.fromisoformat(e["inicio"]).timestamp()
        e["eventos"].append({"t": e["inicio"], "que": "Empieza el entrenamiento"})
    e["gpu_hist"], e["energia"] = hist_reducido(t0), energia(t0)
    if not LOG.exists():
        e["fase"] = "esperando"
        return e
    txt = LOG.read_text(encoding="utf-8", errors="replace")
    e["log_hace"] = int(time.time() - LOG.stat().st_mtime)
    if m := re.search(r"(\d+) sesiones de entrenamiento, (\d+) de validación", txt):
        e["sesiones"], e["sesiones_val"] = int(m.group(1)), int(m.group(2))
    for m in re.finditer(r"\{'(?:loss|eval_loss)'[^}]*\}", txt):
        try:
            d = {k: float(v) for k, v in ast.literal_eval(m.group(0)).items()}
        except Exception:
            continue
        (e["examenes"] if "eval_loss" in d else e["perdidas"]).append(d)
    # sin las barras con nombre ("Loading weights: …"): la de entrenamiento y la del examen no lo llevan
    lineas = [l for l in txt.replace("\r", "\n").split("\n") if not re.match(r"\s*[A-Za-z][^|]*:", l)]
    barras = [(int(a), int(b), c, d, float(v), u) for a, b, c, d, v, u in BARRA.findall("\n".join(lineas))]
    total = -(-e["sesiones"] // ACCUM) if "sesiones" in e else None
    tren = [b for b in barras if b[1] == total]
    if tren:
        paso, _, trans, resta, v, u = tren[-1]
        e.update(paso=paso, pasos=total, transcurrido=segundos(trans), restante=segundos(resta),
                 s_paso=v if u == "s/it" else 1 / v)
        e["examinando"] = barras[-1][1] != total and barras[-1][0] < barras[-1][1]
        # duración REAL de cada paso (diferencia entre transcurridos), no la media suavizada de tqdm, que
        # arrastra los pasos lentos durante mucho rato y hacía parecer lento lo que ya iba bien
        fin_de = {}
        for p, _, trans_p, _, _, _ in tren:
            fin_de[p] = segundos(trans_p)
        orden = sorted(fin_de.items())
        reales = [(b[0], b[1] - a[1]) for a, b in zip(orden, orden[1:]) if b[0] == a[0] + 1 and b[1] > a[1]]
        e["velocidades"] = [(p, d / ACCUM) for p, d in reales]
        if reales:
            ultimos = sorted(d for _, d in reales[-5:])
            tipico = ultimos[len(ultimos) // 2]                 # mediana de los 5 últimos pasos
            e["s_paso"] = tipico
            e["restante"] = int((total - paso) * tipico)
    for i, x in enumerate(e["examenes"]):
        e["eventos"].append({"paso": round(x.get("epoch", 0) * (total or 0)), "que": f"Examen {i + 1}: error {x['eval_loss']:.3f}"})
    if ADAPTADOR.exists():
        for c in sorted(ADAPTADOR.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime):
            e["eventos"].append({"t": datetime.fromtimestamp(c.stat().st_mtime).isoformat(timespec="seconds"),
                                 "que": f"Copia de seguridad guardada (paso {c.name.split('-')[-1]})"})
    if m := re.search(r"pérdida final en vidas apartadas: ([\d.]+)", txt):
        e["examen_final"] = float(m.group(1))
        e["eventos"].append({"que": f"Examen final: error {e['examen_final']:.3f}"})
    ultimas = "\n".join(txt.split("\n")[-8:])
    if "Traceback" in txt or re.search(r"\b\w*Error\b", ultimas):
        e["fase"], e["error"] = "error", txt[-1500:]
    elif FIN.exists() or "adaptador guardado" in txt:
        e["fase"] = "terminado"
        e["eventos"].append({"que": "Adaptador guardado: ¡terminado!"})
    elif "paso" in e:
        e["fase"] = "entrenando" if e["log_hace"] < 600 else "parado"
    else:
        e["fase"] = "preparando"
    return e


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/estado":
            cuerpo, tipo = json.dumps(estado()).encode(), "application/json"
        elif u.path == "/vida.json":                      # partida en directo de training/evalua_ia.py
            f = DATA / "evalua_ia" / "en_directo.json"
            cuerpo, tipo = (f.read_bytes() if f.exists() else b"{}"), "application/json"
        elif u.path in ("/vida", "/vida.html"):
            cuerpo, tipo = (AQUI / "vida.html").read_bytes(), "text/html; charset=utf-8"
        elif u.path == "/log":
            n = int(parse_qs(u.query).get("n", ["400"])[0])
            txt = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
            cuerpo, tipo = json.dumps(lineas_log(txt)[-n:]).encode(), "application/json"
        else:
            cuerpo, tipo = (AQUI / "index.html").read_bytes(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=muestreador, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
