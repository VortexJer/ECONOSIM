"""¿Las noticias (leídas por FinBERT) anticipan algo? Antes de dejar que el robot las use.

Cada fin de mes t (2009-2020) y cada empresa del S&P 500 de ESE momento con precio:
  tono = nº buenas − nº malas conocidas en (t−30 días, t], sin las de tipo "precio"
  futuro = rendimiento de t a t+21 sesiones MENOS el del SPY
Se mide: rendimiento futuro medio por grupo de tono, IC (correlación de rangos) mensual y su
t, y la regla concreta del robot: entre las 8 primeras por momentum, ¿las de noticias netas
malas lo hacen peor que las demás?

Uso: training/.venv/Scripts/python.exe training/estudio_noticias.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    n = pd.read_parquet(ROOT / "data" / "news" / "scored.parquet", columns=["symbol", "conocida", "tipo", "tono"])
    n = n[n.tipo != "precio"]
    n["conocida"] = n.conocida.dt.tz_convert("UTC").dt.tz_localize(None)
    n["v"] = n.tono.map({"buena": 1, "mala": -1, "neutra": 0})
    p = pd.read_parquet(ROOT / "data" / "pit" / "prices.parquet", columns=["date", "ticker", "adj_close"])
    p["date"] = pd.to_datetime(p.date)
    p = p[(p.date >= "2008-01-01") & (p.date <= "2021-01-31")]
    adj = p.pivot_table(index="date", columns="ticker", values="adj_close")
    m = pd.read_csv(ROOT / "data" / "pit" / "members.csv", parse_dates=["start_date", "end_date"])
    m["end_date"] = m.end_date.fillna(pd.Timestamp("2100-01-01"))
    days = adj.index
    fechas = [days[days.searchsorted(pd.Timestamp(y, mo, 1)) - 1]
              for y in range(2009, 2021) for mo in range(1, 13)
              if pd.Timestamp("2009-04-01") <= pd.Timestamp(y, mo, 1) <= pd.Timestamp("2020-06-01")]
    filas, ics = [], []
    for t in fechas:
        i = days.get_loc(t)
        if i + 21 >= len(days):
            break
        miembros = set(m[(m.start_date <= t) & (m.end_date > t)].ticker) & set(adj.columns)
        ventana = n[(n.conocida > t - pd.Timedelta(days=30)) & (n.conocida <= t + pd.Timedelta(hours=23))]
        tono = ventana.groupby("symbol").v.agg(["sum", "count"])
        spy = adj["SPY"].iat[i + 21] / adj["SPY"].iat[i] - 1
        mom = adj.iloc[i - 21] / adj.iloc[i - 252] - 1 if i >= 252 else None
        sub = []
        for s in miembros:
            a0, a1 = adj[s].iat[i], adj[s].iat[i + 21]
            if np.isnan(a0) or np.isnan(a1):
                continue
            neto = tono["sum"].get(s, 0)
            sub.append({"t": t, "s": s, "neto": neto, "hay": tono["count"].get(s, 0) > 0,
                        "fut": a1 / a0 - 1 - spy, "mom": np.nan if mom is None else mom.get(s, np.nan)})
        d = pd.DataFrame(sub)
        con = d[d.hay]
        if len(con) > 30:
            ics.append(con.neto.rank().corr(con.fut.rank()))
        filas.append(d)
    d = pd.concat(filas)
    ics = np.array(ics)
    print(f"{len(fechas)} meses · {len(d):,} empresa-mes · {d.hay.mean() * 100:.0f} % con alguna noticia\n")
    d["grupo"] = np.select([~d.hay, d.neto < 0, d.neto == 0], ["sin noticias", "netas malas", "empate"], "netas buenas")
    g = d.groupby("grupo").fut.agg(["count", "mean", "median"])
    g["mean"] *= 100; g["median"] *= 100
    print("Rendimiento del mes siguiente frente al SPY (puntos %):")
    print(g.round(2).to_string(), "\n")
    print(f"IC mensual (tono neto vs futuro, solo con noticias): media {ics.mean():+.4f} · "
          f"t = {ics.mean() / ics.std(ddof=1) * np.sqrt(len(ics)):+.2f} · positivo {np.mean(ics > 0) * 100:.0f} % de meses\n")
    top = d.dropna(subset=["mom"]).sort_values(["t", "mom"], ascending=[True, False]).groupby("t").head(8)
    veto = top.neto < 0
    print("Regla del robot, entre las 8 primeras por momentum cada mes:")
    print(f"  con noticias netas malas ({veto.sum()}): {top[veto].fut.mean() * 100:+.2f} pts/mes")
    print(f"  resto ({(~veto).sum()}):                  {top[~veto].fut.mean() * 100:+.2f} pts/mes")
    dif = top[veto].fut.mean() - top[~veto].fut.mean()
    se = np.sqrt(top[veto].fut.var() / veto.sum() + top[~veto].fut.var() / (~veto).sum())
    print(f"  diferencia {dif * 100:+.2f} pts/mes · t = {dif / se:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
