"""Descarga histórico diario REAL (Yahoo/yfinance) y lo guarda en data/market/.

Un CSV por símbolo (date,open,high,low,close,volume, ajustado por splits/dividendos)
más un manifest con fuente y fecha. La máscara se aplica en tiempo de episodio,
NO aquí: en disco viven los datos reales con su nombre real (solo para el motor).

    python scripts/fetch_market.py            # el universo por defecto, 2004-01-01..hoy
    python scripts/fetch_market.py AAPL MSFT  # símbolos concretos
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "market"

# Universo por defecto: líderes de varios sectores + ETFs de índice, para que las
# correlaciones (tech junta, defensivos aparte, índice = mezcla) sean reales.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",        # tech
    "JPM", "BAC", "GS",                                      # banca
    "XOM", "CVX",                                            # energía
    "JNJ", "PFE", "UNH",                                     # salud
    "KO", "PG", "WMT",                                       # consumo defensivo
    "DIS", "NKE",                                            # consumo discrecional
    "CAT", "BA",                                             # industrial
    "SPY", "QQQ", "DIA", "IWM",                              # ETFs de índice
]
START = "2004-01-01"


def fetch(symbols: list[str], start: str = START) -> dict:
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    OUT.mkdir(parents=True, exist_ok=True)
    saved = {}
    for sym in symbols:
        df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            print(f"  {sym}: SIN DATOS")
            continue
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        path = OUT / f"{sym}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("date,open,high,low,close,volume\n")
            for ts, row in df.iterrows():
                f.write(f"{ts.strftime('%Y-%m-%d')},{row.open:.6f},{row.high:.6f},"
                        f"{row.low:.6f},{row.close:.6f},{int(row.volume)}\n")
        saved[sym] = {"rows": len(df), "first": df.index[0].strftime("%Y-%m-%d"),
                      "last": df.index[-1].strftime("%Y-%m-%d")}
        print(f"  {sym}: {len(df)} filas {saved[sym]['first']}..{saved[sym]['last']}")
    manifest = {
        "_source": {"provider": "Yahoo Finance vía yfinance", "auto_adjust": True,
                    "note": "precios ajustados por splits y dividendos; OHLCV diario",
                    "retrieved": end, "start": start},
        "symbols": saved,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    syms = sys.argv[1:] or UNIVERSE
    m = fetch(syms)
    print(f"OK {len(m['symbols'])} símbolos en {OUT}")
