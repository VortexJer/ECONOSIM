"""Lee cada titular UNA vez con FinBERT y lo deja como "buena / mala / neutra + tipo".

FinBERT (ProsusAI/finbert, abierto) está preentrenado para tono financiero: no se entrena
aquí. El tipo sale de palabras clave del titular (en inglés, como la fuente).

Nada de lo que se guarda mira al futuro:
  * `conocida` = cuándo se SABE la noticia. Si la fuente solo da el día (00:00), cuenta desde
    el día siguiente; si trae hora, desde esa hora.
  * El tipo `precio` marca titulares que solo cuentan lo que ya hizo la cotización ("shares
    are trading higher", listas de "movers", máximos de 52 semanas): no son información nueva,
    son el precio contado con palabras. Se guardan, pero el robot no los usa.

Uso: training/.venv/Scripts/python.exe training/noticias.py
     (data/news/headlines.parquet -> data/news/scored.parquet)
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

NEWS = Path(__file__).resolve().parent.parent / "data" / "news"
MODEL = "ProsusAI/finbert"
TIPOS = [   # el primero que case gana: el orden importa
    ("precio", r"shares (are )?(trading|moving|up|down|higher|lower)|stocks? moving|biggest movers|52-week|"
               r"mid-day|pre-market|premarket|after-hours|session|gainers|losers|stock is (up|down)|shares (rise|fall|jump|drop|slide|surge|gain|climb|sink)"),
    ("analistas", r"maintains|upgrade|downgrade|price target|initiates|reiterates|coverage|\bpt\b|outperform|underperform|overweight|underweight|analyst"),
    ("resultados", r"earnings|\beps\b|revenue|\bq[1-4]\b|quarter|guidance|results|sales|profit|loss|beats|misses|forecast|outlook"),
    ("corporativa", r"acqui|merger|merge|buyback|repurchase|dividend|spin-?off|stake|\bdeal\b|takeover|\bbid\b|split|ipo|offering"),
    ("legal", r"lawsuit|\bsec\b|probe|investigation|\bfine\b|settle|recall|\bfda\b|antitrust|court|charged|fraud|subpoena"),
    ("directivos", r"\bceo\b|\bcfo\b|resign|appoint|names .* (chief|president)|steps down|executive"),
]


def tipo(titular: str) -> str:
    t = titular.lower()
    for nombre, rx in TIPOS:
        if re.search(rx, t):
            return nombre
    return "general"


def main() -> int:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if not torch.cuda.is_available():
        raise SystemExit("FinBERT va en la GPU; no hay GPU visible")
    df = pd.read_parquet(NEWS / "headlines.parquet")
    print(f"{len(df):,} titulares", flush=True)
    tok = AutoTokenizer.from_pretrained(MODEL)
    m = AutoModelForSequenceClassification.from_pretrained(MODEL).cuda().half().eval()
    lab = m.config.id2label                                   # {0: positive, 1: negative, 2: neutral}
    ipos = next(i for i, v in lab.items() if v == "positive")
    ineg = next(i for i, v in lab.items() if v == "negative")
    titulares = df.titular.tolist()
    orden = np.argsort([len(t) for t in titulares])          # lotes de longitud parecida: más rápido
    P = np.zeros((len(titulares), 3), dtype=np.float32)
    t0, B = time.time(), 512
    with torch.no_grad():
        for k in range(0, len(orden), B):
            idx = orden[k:k + B]
            x = tok([titulares[i] for i in idx], padding=True, truncation=True, max_length=64,
                    return_tensors="pt").to("cuda")
            P[idx] = m(**x).logits.float().softmax(-1).cpu().numpy()
            if k // B % 400 == 0:
                print(f"  {k:,}/{len(orden):,} · {time.time() - t0:.0f} s", flush=True)
    df["p_buena"], df["p_mala"] = P[:, ipos], P[:, ineg]
    df["tono"] = np.select([df.p_buena >= 0.6, df.p_mala >= 0.6], ["buena", "mala"], "neutra")
    df["tipo"] = [tipo(t) for t in titulares]
    # cuándo se sabe: con hora -> esa hora; solo el día -> el día siguiente a las 00:00 UTC
    df["conocida"] = df.fecha.where(~df.solo_dia, df.fecha.dt.normalize() + pd.Timedelta(days=1))
    df.to_parquet(NEWS / "scored.parquet", index=False)
    print(f"guardado {NEWS / 'scored.parquet'}")
    print(df.groupby(["tipo", "tono"]).size().unstack(fill_value=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
