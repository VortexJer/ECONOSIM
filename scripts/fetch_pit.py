"""Universo SIN sesgo de supervivencia: quién estaba en el S&P 500 cada día y sus precios.

- Composición histórica (1996-hoy): github.com/fja05680/sp500 (`sp500_ticker_start_end.csv`).
- Precios diarios de miembros actuales Y retirados: github.com/Johnbrick123/sp500-data
  (`prices.parquet`: Yahoo + el histórico de empresas retiradas que conserva Tiingo).
  Aviso del propio conjunto: ~588 de 1.231 miembros históricos no tienen precios (Yahoo los purgó);
  `training/sesgo.py` cuenta cuántos faltan cada año en vez de esconderlo.

Uso: python scripts/fetch_pit.py   (deja data/pit/members.csv y data/pit/prices.parquet, ~150 MB)
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "pit"
FUENTES = {
    "members.csv": "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv",
    "prices.parquet": "https://github.com/Johnbrick123/sp500-data/releases/download/data/prices.parquet",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for nombre, url in FUENTES.items():
        dst = OUT / nombre
        if dst.exists() and "--force" not in sys.argv:
            print(f"ya está {dst.name} ({dst.stat().st_size / 1e6:.1f} MB)")
            continue
        print(f"bajando {nombre} …", flush=True)
        urllib.request.urlretrieve(url, dst)
        print(f"  {dst.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
