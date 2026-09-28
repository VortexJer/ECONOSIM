"""Titulares de noticias históricas por empresa (FNSPID, 1999-2023, CC BY 4.0).

Fuente: huggingface.co/datasets/Zihan1004/FNSPID (`Stock_news/All_external.csv`, 5,7 GB).
Se lee EN STREAMING y solo se guarda lo necesario (fecha, titular, símbolo, editor) de las
empresas que alguna vez estuvieron en el S&P 500 (data/pit/members.csv) más los fondos.
No se guarda el cuerpo del artículo.

Cuándo se SABE una noticia (para no hacer trampa): FNSPID trae la hora en UTC; si viene a
00:00:00 es que solo se conoce el día, y se tratará como conocida al día siguiente
(`training/noticias.py` lo aplica; aquí se guarda la marca `solo_dia`).

Uso: python scripts/fetch_news.py   ->  data/news/headlines.parquet
"""
from __future__ import annotations

import csv
import io
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news"
URL = "https://huggingface.co/datasets/Zihan1004/FNSPID/resolve/main/Stock_news/All_external.csv"
FONDOS = {"SPY", "QQQ", "DIA", "IWM", "SH", "PSQ", "DOG", "RWM"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    simbolos = set(pd.read_csv(ROOT / "data" / "pit" / "members.csv").ticker) | FONDOS
    csv.field_size_limit(1 << 30)
    filas, leidas, t0 = [], 0, time.time()
    with urllib.request.urlopen(URL) as r:
        total = int(r.headers.get("Content-Length") or 0)
        crudo = io.BufferedReader(r, buffer_size=1 << 20)
        texto = io.TextIOWrapper(crudo, encoding="utf-8", errors="replace", newline="")
        for fila in csv.DictReader(texto):
            leidas += 1
            sym = (fila.get("Stock_symbol") or "").strip().upper()
            titulo = (fila.get("Article_title") or "").strip()
            if sym in simbolos and titulo:
                fecha = (fila.get("Date") or "").strip()
                filas.append((fecha, sym, titulo, (fila.get("Publisher") or "").strip()))
            if leidas % 200_000 == 0:
                print(f"{leidas:,} filas leídas · {len(filas):,} guardadas · {time.time() - t0:.0f} s"
                      f"{f' · de {total / 1e9:.1f} GB' if total else ''}", flush=True)
    df = pd.DataFrame(filas, columns=["fecha", "symbol", "titular", "editor"])
    df["solo_dia"] = df.fecha.str.contains(" 00:00:00")
    df["fecha"] = pd.to_datetime(df.fecha.str.replace(" UTC", ""), errors="coerce", utc=True)
    df = df.dropna(subset=["fecha"]).drop_duplicates(["symbol", "titular"]).sort_values("fecha")
    df.to_parquet(OUT / "headlines.parquet", index=False)
    print(f"guardado {len(df):,} titulares de {df.symbol.nunique()} símbolos "
          f"({df.fecha.min().date()} → {df.fecha.max().date()}) en {OUT / 'headlines.parquet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
