"""Comprobaciones de training/SOBREMESA.md. Uso: python verify.py <gate>. Imprime 'VERIFICADO <gate>' solo si todo pasa."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TR = ROOT / "training"


def fallo(msg: str) -> None:
    print("FALLO:", msg)
    sys.exit(1)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def entorno() -> None:
    import bitsandbytes, peft, torch, transformers
    if not torch.cuda.is_available():
        fallo("torch no ve la GPU")
    pedido = {"torch": "2.6.0", "transformers": "5.17.0", "peft": "0.20.0", "bitsandbytes": "0.50.2"}
    real = {"torch": torch.__version__.split("+")[0], "transformers": transformers.__version__,
            "peft": peft.__version__, "bitsandbytes": bitsandbytes.__version__}
    if real != pedido:
        fallo(f"versiones {real} != {pedido}")
    if sys.version_info[:2] != (3, 13):
        fallo(f"python {sys.version_info[:2]}")
    print("GPU", torch.cuda.get_device_name(0))


def mercado() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from fetch_market import UNIVERSE
    hoy = date.today()
    for s in UNIVERSE:
        p = ROOT / "data" / "market" / f"{s}.csv"
        if not p.exists():
            fallo(f"falta {p}")
        filas = p.read_text(encoding="utf-8").strip().splitlines()
        if len(filas) < 1000:
            fallo(f"{s}: solo {len(filas)} filas")
        ultimo = date.fromisoformat(filas[-1].split(",")[0])
        if ultimo < hoy - timedelta(days=10):
            fallo(f"{s}: último dato {ultimo}")
    print(f"{len(UNIVERSE)} símbolos al día")


def noticias() -> None:
    import pandas as pd
    h = pd.read_parquet(ROOT / "data" / "news" / "headlines.parquet")
    s = pd.read_parquet(ROOT / "data" / "news" / "scored.parquet")
    if len(h) < 100_000 or len(s) != len(h):
        fallo(f"titulares {len(h)} vs puntuados {len(s)}")
    for c in ("p_buena", "p_mala", "tono", "tipo", "conocida"):
        if c not in s.columns:
            fallo(f"falta columna {c}")
    tonos = s.tono.value_counts()
    if set(tonos.index) - {"buena", "mala", "neutra"} or min(tonos.get(t, 0) for t in ("buena", "mala", "neutra")) == 0:
        fallo(f"tonos raros {dict(tonos)}")
    print(len(s), "titulares puntuados", dict(tonos))


def demos() -> None:
    meta = [json.loads(l) for l in (TR / "data" / "demos_meta.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(meta) != 1000:
        fallo(f"{len(meta)} vidas, se pidieron 1000")
    limpias = [m for m in meta if m["errores"] == 0 and m["alive"]]
    if len(limpias) / len(meta) < 0.95:
        fallo(f"solo {len(limpias)}/{len(meta)} vidas sin errores")
    esperadas = sum(m["sesiones"] for m in limpias)
    n = 0
    with open(TR / "data" / "demos.jsonl", encoding="utf-8") as f:
        for linea in f:
            r = json.loads(linea)
            if not r.get("messages"):
                fallo(f"sesión {n} sin mensajes")
            n += 1
    if n != esperadas or n < 1000:
        fallo(f"{n} sesiones en demos.jsonl, meta dice {esperadas}")
    print(f"{len(limpias)}/{len(meta)} vidas limpias, {n} sesiones")


def velocidad() -> None:
    if not (TR / "adapters" / "prueba" / "adapter_model.safetensors").exists():
        fallo("no hay adaptador de prueba")
    log = (TR / "data" / "prueba.log").read_text(encoding="utf-8", errors="replace")
    its = re.findall(r"([\d.]+)s/it", log)
    if not its or "adaptador guardado" not in log:
        fallo("el log de la prueba no tiene s/it o no terminó")
    print("s/it medidos:", its[-3:])


def perdida_final() -> float:
    log = (TR / "data" / "train_v1.log").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"pérdida final en vidas apartadas: ([\d.]+)", log)
    if not m or "adaptador guardado" not in log:
        fallo("train_v1.log no terminó con pérdida de validación")
    v = float(m.group(1))
    if not math.isfinite(v) or not 0 < v < 5:
        fallo(f"pérdida de validación rara: {v}")
    return v


def entrenamiento() -> None:
    p = TR / "adapters" / "demos-v1" / "adapter_model.safetensors"
    if not p.exists() or p.stat().st_size < 1_000_000:
        fallo("no hay adaptador demos-v1")
    print("pérdida validación", perdida_final())


def resultados() -> None:
    txt = (TR / "RESULTADOS.md").read_text(encoding="utf-8")
    m = re.search(r"^## 9\. Imitación demos-v1.*?(?=^## |\Z)", txt, re.S | re.M)
    if not m:
        fallo("falta la sección 9")
    sec = m.group(0)
    v = perdida_final()
    if not any(f"{v:.{k}f}".replace(".", ",") in sec or f"{v:.{k}f}" in sec for k in (2, 3, 4)):
        fallo(f"la sección 9 no recoge la pérdida de validación medida {v}")
    if "3050" not in sec:
        fallo("la sección 9 no dice la GPU")
    print("sección 9 coherente con el log")


def publicado() -> None:
    if not git("ls-files", "training/train_qlora.py").strip():
        fallo("control positivo: git ls-files no ve train_qlora.py")
    if git("ls-files", "training/adapters").strip():
        fallo("hay adaptadores dentro de git")
    if "## 9. Imitación demos-v1" not in git("show", "HEAD:training/RESULTADOS.md"):
        fallo("el último commit no tiene la sección 9")
    git("fetch", "-q", "origin")
    rama = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if git("rev-parse", "HEAD").strip() != git("rev-parse", f"origin/{rama}").strip():
        fallo("HEAD no está en GitHub")
    print("commit publicado en origin/" + rama)


if __name__ == "__main__":
    g = sys.argv[1]
    globals()[g]()
    print("VERIFICADO", g)
