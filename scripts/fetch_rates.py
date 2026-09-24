"""Descarga series macro REALES de FRED que usan las opciones del gemelo.

  * DTB3   — letra del Tesoro a 3 meses (tipo sin riesgo de Black-Scholes, en %).
  * VIXCLS — índice de volatilidad implícita del S&P 500 (para escalar la volatilidad
             realizada a implícita: la prima de riesgo de varianza real de cada día).

Se guardan en data/rates/<ID>.csv (fecha,valor) desde 2004, sin huecos de "." (FRED
marca así los festivos). Uso: python scripts/fetch_rates.py
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rates"
SERIES = {"DTB3": "letra del Tesoro a 3 meses, % anual", "VIXCLS": "VIX, volatilidad implícita del S&P 500, % anual"}
DESDE = "2004-01-01"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"_source": {"provider": "FRED (St. Louis Fed)", "retrieved": date.today().isoformat()}, "series": {}}
    for sid, desc in SERIES.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
        raw = urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
        filas = []
        for row in csv.DictReader(io.StringIO(raw)):
            d, v = row["observation_date"], row[sid]
            if d >= DESDE and v not in (".", ""):
                filas.append((d, float(v)))
        with open(OUT / f"{sid}.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "value"])
            w.writerows(filas)
        manifest["series"][sid] = {"desc": desc, "rows": len(filas), "first": filas[0][0], "last": filas[-1][0]}
        print(sid, len(filas), filas[0][0], "->", filas[-1][0])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
