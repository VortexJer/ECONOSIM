"""Panel diario para buscar estrategias agresivas SIN mirar el futuro ni el sesgo de supervivencia.

Universo: quien estaba en el S&P 500 ESE día (data/pit/members.csv) con precios de miembros actuales y
retirados (prices.parquet de fetch_pit.py). Fondos y fondos inversos de data/market. VIX y letras del
Tesoro de data/rates. Noticias puntuadas (data/news/scored.parquet, cuentan desde que se conocen).
Cuentas de ~90 empresas con su fecha de publicación (data/fundamentals, nada antes de publicarse).

Cada indicador es una matriz fechas x empresas; el día t solo usa datos hasta el cierre de t.
    python -m agresivo.datos   ->  training/data/agresivo/panel.npz
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PIT_PRECIOS = Path(r"C:\Users\escri\Downloads\econosim-pesado\data\pit\prices.parquet")
OUT = HERE.parent / "data" / "agresivo"
DESDE = "2004-01-01"
FONDOS = ["SPY", "QQQ", "IWM", "DIA", "SH", "PSQ", "DOG", "RWM"]


def mercado(sym: str) -> pd.Series:
    df = pd.read_csv(ROOT / "data" / "market" / f"{sym}.csv", parse_dates=["date"]).set_index("date")
    return df["close"]


def fundamentales(fechas: pd.DatetimeIndex, tickers: list[str], cierre: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Crecimiento de beneficio y ventas (último trimestre vs el mismo del año anterior), margen neto,
    rentabilidad sobre fondos propios y E/P: cada dato vale desde su fecha de publicación."""
    salida = {k: pd.DataFrame(np.nan, index=fechas, columns=tickers) for k in
              ("crec_benef", "crec_ventas", "margen", "roe", "ep")}
    for f in (ROOT / "data" / "fundamentals").glob("*.json"):
        t = f.stem.replace("-", ".") if f.stem.replace("-", ".") in tickers else f.stem
        if t not in tickers:
            continue
        hechos = pd.DataFrame(json.loads(f.read_text(encoding="utf-8"))["hechos"])
        if hechos.empty:
            continue
        hechos = hechos[hechos.forma.isin(["10-Q", "10-K"])].copy()
        hechos["inicio"] = pd.to_datetime(hechos.inicio); hechos["fin"] = pd.to_datetime(hechos.fin)
        hechos["publicado"] = pd.to_datetime(hechos.publicado)
        hechos["dias"] = (hechos.fin - hechos.inicio).dt.days
        trim = hechos[hechos.dias.between(80, 100) & hechos.campo.isin(["net_income", "revenue", "eps_diluted"])]
        trim = trim.sort_values("publicado").drop_duplicates(["campo", "fin"], keep="first")
        balance = hechos[hechos.campo == "equity"].sort_values("publicado").drop_duplicates("fin", keep="first")
        filas = []
        for (campo), g in trim.groupby("campo"):
            g = g.set_index("fin").sort_index()
            for fin, r in g.iterrows():
                previo = g.loc[(g.index >= fin - pd.Timedelta(days=380)) & (g.index <= fin - pd.Timedelta(days=350))]
                ttm = g.loc[(g.index > fin - pd.Timedelta(days=360)) & (g.index <= fin), "val"]
                filas.append({"campo": campo, "fin": fin, "publicado": r.publicado, "val": r.val,
                              "previo": previo.val.iloc[-1] if len(previo) else np.nan,
                              "ttm": ttm.sum() if len(ttm) == 4 else np.nan})
        if not filas:
            continue
        ev = pd.DataFrame(filas)

        def serie(valores: pd.DataFrame, col: str) -> pd.Series:
            s = valores.sort_values("publicado").drop_duplicates("publicado", keep="last").set_index("publicado")[col]
            return s.reindex(fechas, method="ffill")
        ni, rv, eps = (ev[ev.campo == c] for c in ("net_income", "revenue", "eps_diluted"))
        if len(ni):
            g = ni.assign(x=(ni.val - ni.previo) / ni.previo.abs())
            salida["crec_benef"][t] = serie(g, "x")
        if len(rv):
            g = rv.assign(x=(rv.val - rv.previo) / rv.previo.abs())
            salida["crec_ventas"][t] = serie(g, "x")
        if len(ni) and len(rv):
            m = ni.merge(rv, on="fin", suffixes=("_n", "_r"))
            m = m.assign(x=m.ttm_n / m.ttm_r, publicado=m[["publicado_n", "publicado_r"]].max(axis=1))
            salida["margen"][t] = serie(m, "x")
        if len(ni) and len(balance):
            b = balance.set_index("fin").val.sort_index()
            m = ni.assign(fondos_propios=[b.asof(fn) if len(b.loc[:fn]) else np.nan for fn in ni.fin])
            m = m.assign(x=m.ttm / m.fondos_propios)
            salida["roe"][t] = serie(m, "x")
        if len(eps):
            s = serie(eps, "ttm")
            salida["ep"][t] = s / cierre[t]
    for k in salida:
        salida[k] = salida[k].replace([np.inf, -np.inf], np.nan).clip(-5, 5)
    return salida


def noticias(fechas: pd.DatetimeIndex, tickers: list[str]) -> pd.DataFrame:
    """Tono neto de los últimos 30 días (buenas - malas, sin las de 'precio'), desde que se conocen."""
    n = pd.read_parquet(ROOT / "data" / "news" / "scored.parquet", columns=["symbol", "tono", "tipo", "conocida"])
    n = n[(n.tipo != "precio") & n.symbol.isin(tickers)]
    n["dia"] = pd.to_datetime(n.conocida).dt.tz_localize(None).dt.normalize()
    n["neto"] = (n.tono == "buena").astype(int) - (n.tono == "mala").astype(int)
    diario = n.pivot_table(index="dia", columns="symbol", values="neto", aggfunc="sum").reindex(columns=tickers)
    diario = diario.reindex(pd.date_range(diario.index.min(), fechas.max()), fill_value=0).fillna(0)
    tono = diario.rolling(30, min_periods=1).sum().reindex(fechas)
    tono[(fechas < diario.index.min()) | (fechas > pd.Timestamp("2020-06-11"))] = np.nan   # fuera de cobertura: sin dato
    return tono


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("precios…", flush=True)
    p = pd.read_parquet(PIT_PRECIOS, columns=["date", "ticker", "adj_close", "close", "volume"])
    p = p[p.date >= DESDE]
    spy = mercado("SPY")
    fechas = spy.index[spy.index >= DESDE]
    ac = p.pivot_table(index="date", columns="ticker", values="adj_close").reindex(fechas)
    cl = p.pivot_table(index="date", columns="ticker", values="close").reindex(fechas)
    vo = p.pivot_table(index="date", columns="ticker", values="volume").reindex(fechas)
    tickers = list(ac.columns)
    print(f"  {len(fechas)} días x {len(tickers)} empresas", flush=True)

    # quién estaba en el índice cada día
    mem = pd.read_csv(ROOT / "data" / "pit" / "members.csv", parse_dates=["start_date", "end_date"])
    miembro = pd.DataFrame(False, index=fechas, columns=tickers)
    for r in mem.itertuples():
        if r.ticker in miembro.columns:
            fin = r.end_date if pd.notna(r.end_date) else fechas.max()
            miembro.loc[(fechas >= r.start_date) & (fechas <= fin), r.ticker] = True

    print("indicadores de precio…", flush=True)
    ret = ac.pct_change(fill_method=None)
    spy_ret = spy.reindex(fechas).pct_change()
    ind = {
        "r1": ret,
        "r5": ac / ac.shift(5) - 1,
        "r21": ac / ac.shift(21) - 1,
        "mom6": ac.shift(21) / ac.shift(126) - 1,
        "mom12": ac.shift(21) / ac.shift(252) - 1,
        "vol20": ret.rolling(20, min_periods=15).std(),
        "dsma50": ac / ac.rolling(50, min_periods=40).mean() - 1,
        "dsma200": ac / ac.rolling(200, min_periods=150).mean() - 1,
        "max21": ret.rolling(21, min_periods=15).max(),
        "alto52": ac / ac.rolling(252, min_periods=200).max() - 1,
        "volz": np.log((vo + 1) / (vo.rolling(20, min_periods=15).mean() + 1)),
    }
    sube = ret.clip(lower=0).rolling(14, min_periods=10).mean()
    baja = (-ret.clip(upper=0)).rolling(14, min_periods=10).mean()
    ind["rsi14"] = 100 - 100 / (1 + sube / (baja + 1e-12))
    cov = ret.rolling(60, min_periods=40).cov(spy_ret)
    ind["beta60"] = cov.div(spy_ret.rolling(60, min_periods=40).var(), axis=0)
    print("cuentas publicadas…", flush=True)
    ind.update(fundamentales(fechas, tickers, cl))
    print("noticias…", flush=True)
    ind["noticias"] = noticias(fechas, tickers)

    # quién se puede comprar: en el índice, con precio > 5 $ y más de 10 M$ negociados al día (mediana 20 d)
    dolares = (cl * vo).rolling(20, min_periods=15).median()
    apto = miembro & cl.notna() & (cl > 5) & (dolares > 1e7) & ac.notna()

    # mercado entero: lo que usa el bot para decidir cuándo ir en contra del mercado
    vix = pd.read_csv(ROOT / "data" / "rates" / "VIXCLS.csv", parse_dates=["date"]).set_index("date").value
    letra = pd.read_csv(ROOT / "data" / "rates" / "DTB3.csv", parse_dates=["date"]).set_index("date").value
    s = spy.reindex(fechas)
    regimen = pd.DataFrame({
        "spy_dsma200": s / s.rolling(200).mean() - 1,
        "spy_dsma50": s / s.rolling(50).mean() - 1,
        "spy_r21": s / s.shift(21) - 1,
        "spy_r5": s / s.shift(5) - 1,
        "vix": pd.to_numeric(vix, errors="coerce").reindex(fechas, method="ffill"),
        "letra": pd.to_numeric(letra, errors="coerce").reindex(fechas, method="ffill"),
    }, index=fechas)
    regimen["vix_cambio5"] = regimen.vix / regimen.vix.shift(5) - 1
    fondos = pd.DataFrame({f: mercado(f).reindex(fechas) for f in FONDOS})

    np.savez_compressed(
        OUT / "panel.npz", fechas=fechas.values.astype("datetime64[D]"), tickers=np.array(tickers),
        precio=ac.values.astype(np.float32), apto=apto.values, nombres=np.array(list(ind)),
        indicadores=np.stack([ind[k].reindex(index=fechas, columns=tickers).values.astype(np.float32) for k in ind]),
        regimen=regimen.values.astype(np.float32), regimen_nombres=np.array(list(regimen.columns)),
        fondos=fondos.values.astype(np.float32), fondos_nombres=np.array(FONDOS))
    cobertura = {k: float(np.isfinite(ind[k].where(apto).values).sum() / apto.values.sum()) for k in ind}
    print("cobertura de cada indicador entre las empresas comprables:", json.dumps(cobertura, indent=1))
    print("empresas comprables por año:", apto.sum(axis=1).groupby(fechas.year).mean().round(0).to_dict())
    print(f"guardado {OUT / 'panel.npz'}")


if __name__ == "__main__":
    main()
