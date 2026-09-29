"""Vigila el entrenamiento y SALE con una línea en cuanto hay algo que atender (así avisa a Claude):
LENTO (2 pasos seguidos > 140 s), ERROR, PARADO (15 min sin escribir) o TERMINADO."""
import re
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "training" / "data"
LOG, FIN = DATA / "train_v1.log", DATA / "train_v1.fin"
BARRA = re.compile(r"(\d+)/268 \[(\d+:)?(\d+):(\d+)<")


def pasos(txt: str) -> list[tuple[int, int]]:
    """(paso, segundos transcurridos) de la barra de entrenamiento, uno por paso."""
    vistos = {}
    for m in BARRA.finditer(txt.replace("\r", "\n")):
        h = int(m.group(2)[:-1]) if m.group(2) else 0
        vistos[int(m.group(1))] = h * 3600 + int(m.group(3)) * 60 + int(m.group(4))
    return sorted(vistos.items())


sys.stdout.reconfigure(encoding="utf-8")                   # el log trae caracteres que cp1252 no sabe escribir
desde = int(sys.argv[1]) if len(sys.argv) > 1 else 0       # no volver a avisar de pasos ya atendidos
while True:
    txt = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
    if "Traceback" in txt:
        print("ERROR", txt[-800:].replace("\n", " | ")); break
    if "adaptador guardado" in txt or FIN.exists():          # entrenar.ps1 borra la marca al empezar
        print("TERMINADO", (FIN.read_text(encoding="utf-8-sig").strip() if FIN.exists() else "")); break
    if LOG.exists() and time.time() - LOG.stat().st_mtime > 900:
        print("PARADO: 15 min sin escribir en el log"); break
    ps = [p for p in pasos(txt) if p[0] > desde]
    dur = [(b[0], b[1] - a[1]) for a, b in zip(ps, ps[1:]) if b[0] == a[0] + 1]
    if len(dur) >= 2 and all(d > 140 for _, d in dur[-2:]):
        print("LENTO", " ".join(f"paso {p}: {d} s" for p, d in dur[-2:])); break
    time.sleep(60)
