"""Lo que se puede clonar de los grandes, con REGLAS PUBLICADAS y SIN optimizar pesos:

  A) Seguimiento de tendencia multimercado (Moskowitz, Ooi y Pedersen 2012): cada mes, en cada uno de
     15 fondos (acciones EE. UU./mundo/emergentes, bonos, crédito, oro, plata, materias primas, petróleo,
     inmobiliario, dólar) se compra si su rentabilidad de 12 meses supera a la letra del Tesoro; si no,
     efectivo. Tamaño inverso a la volatilidad de cada mercado (60 días).
  B) Acciones con varios factores a la vez, repartidas en 50 empresas del S&P 500 de ESE día:
     momentum 12-1 + baja volatilidad + barato por beneficios (donde haya cuentas), a partes iguales.
  C) A y B a medias.
Encima, control de volatilidad (lo que hacen AQR y compañía): cada mes se escala la exposición para
apuntar a un 10 %, 20 % o 30 % de volatilidad anual, con apalancamiento de hasta 3x pagando la letra
+ 1 % por lo prestado. Sin comisión; 5 pb de medio diferencial en lo que se rota.
Se publican TODAS las variantes y el Sharpe deflactado por el número de pruebas.

    python -m agresivo.clones
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from agresivo.motor import MEDIO_DIFERENCIAL, Motor

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent.parent / "data" / "agresivo"
MERCADOS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "TIP", "GLD", "SLV", "DBC", "USO", "VNQ", "UUP"]
MES, APAL_MAX, SOBRECOSTE = 21, 3.0, 0.01
OBJETIVOS = [0.10, 0.20, 0.30]
# la parte "a la baja": con la cuenta sin margen, el corto de cada mercado es su fondo inverso (-1x)
INVERSOS = {"SPY": "SH", "QQQ": "PSQ", "IWM": "RWM", "EFA": "EFZ", "EEM": "EUM", "TLT": "TBF"}


def precios(fechas: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({s: pd.read_csv(ROOT / "data" / "market" / f"{s}.csv", parse_dates=["date"])
                         .set_index("date").close.reindex(fechas) for s in MERCADOS + list(INVERSOS.values())})


def tendencia(p: pd.DataFrame, letra_diaria: pd.Series, cortos: bool = False) -> tuple[pd.Series, pd.Series]:
    """Rentabilidad diaria de A (sin apalancar) y su rotación por día. Con cortos=True, la tendencia
    negativa de un mercado compra su fondo inverso (si existe ya ese día); si no, efectivo."""
    base = p[MERCADOS]
    inv = p[[INVERSOS.get(s, s) for s in MERCADOS]].copy(); inv.columns = MERCADOS
    r = base.pct_change(fill_method=None).fillna(0.0)
    r_inv = inv.pct_change(fill_method=None).fillna(0.0)
    p = base
    vol = r.rolling(60, min_periods=40).std() * math.sqrt(252)
    letra12 = (1 + letra_diaria).rolling(252).apply(np.prod, raw=True) - 1
    exceso12 = p / p.shift(252) - 1 - letra12.values[:, None]
    out, rot = np.zeros(len(p)), np.zeros(len(p))
    w = np.zeros(p.shape[1]); cash = 1.0; lado = np.ones(p.shape[1])     # 1 = a favor, -1 = su inverso
    tiene_inv = np.array([s in INVERSOS for s in MERCADOS])
    for t in range(253, len(p)):
        if (t - 253) % MES == 0:
            ok = vol.iloc[t - 1].notna() & p.iloc[t - 1].notna()
            arriba = (exceso12.iloc[t - 1] > 0) & ok
            abajo = (exceso12.iloc[t - 1] <= 0) & ok & tiene_inv & inv.iloc[t - 1].notna() if cortos else ok & False
            nuevo = np.where(arriba | abajo, 1 / vol.iloc[t - 1].clip(lower=0.02), 0.0)
            nuevo = nuevo / nuevo.sum() if nuevo.sum() > 0 else nuevo
            nuevo_lado = np.where(abajo, -1.0, 1.0)
            rot[t] = np.abs(nuevo * nuevo_lado - w * lado).sum()
            w = nuevo; lado = nuevo_lado; cash = 1 - w.sum()
        rt = np.where(lado > 0, r.iloc[t].values, r_inv.iloc[t].values)
        g = float(w @ rt) + cash * letra_diaria.iloc[t]
        out[t] = g
        w = w * (1 + rt) / (1 + g) if (1 + g) > 0 else w
        cash = 1 - w.sum()
    return pd.Series(out, index=p.index), pd.Series(rot, index=p.index)


def factores(m: Motor, fechas: pd.DatetimeIndex, letra_diaria: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Rentabilidad diaria de B (sin apalancar): 50 empresas, momentum + baja volatilidad + valor, mensual."""
    j = {n: i for i, n in enumerate(m.nombres)}
    puntos = m.Z[j["mom12"]] - m.Z[j["vol20"]] + m.Z[j["ep"]]
    P = m.precio
    diaria = np.nan_to_num(P[1:] / P[:-1] - 1, nan=0.0)
    out, rot = np.zeros(len(fechas)), np.zeros(len(fechas))
    idx, w = np.array([], dtype=int), np.array([])
    for t in range(253, len(fechas)):
        if (t - 253) % MES == 0:
            s = np.where(m.apto[t - 1], puntos[t - 1], -np.inf)
            nuevo_idx = np.argpartition(-s, 49)[:50]
            antes = dict(zip(idx.tolist(), w.tolist()))
            rot[t] = sum(abs(0.02 - antes.pop(i, 0.0)) for i in nuevo_idx.tolist()) + sum(antes.values())
            idx, w = nuevo_idx, np.full(50, 0.02)
        rt = diaria[t - 1, idx]
        g = float(w @ rt)
        out[t] = g
        w = w * (1 + rt) / (1 + g) if (1 + g) > 0 else w
    return pd.Series(out, index=fechas), pd.Series(rot, index=fechas)


def controla_volatilidad(r: pd.Series, rot: pd.Series, objetivo: float, letra_diaria: pd.Series) -> pd.Series:
    """Cada mes: apalancamiento = objetivo / volatilidad de los últimos 60 días (tope 3x)."""
    vol = r.rolling(60, min_periods=40).std() * math.sqrt(252)
    out = np.zeros(len(r)); L = 0.0
    for t in range(len(r)):
        if t % MES == 0 and not np.isnan(vol.iloc[t - 1] if t else np.nan):
            nuevo = min(APAL_MAX, objetivo / max(vol.iloc[t - 1], 1e-4))
            out[t] -= abs(nuevo - L) * MEDIO_DIFERENCIAL
            L = nuevo
        prestado = max(0.0, L - 1)
        out[t] += L * r.iloc[t] - prestado * (letra_diaria.iloc[t] + SOBRECOSTE / 252) \
            + max(0.0, 1 - L) * letra_diaria.iloc[t] * 0 - L * rot.iloc[t] * MEDIO_DIFERENCIAL
    return pd.Series(out, index=r.index)


def metricas(r: pd.Series, letra: pd.Series, desde: str, hasta: str) -> dict:
    x = r.loc[desde:hasta]; c = letra.loc[desde:hasta]
    v = (1 + x).cumprod()
    anios = len(x) / 252
    ex = x - c
    return {"anual": float(v.iloc[-1] ** (1 / anios) - 1), "volatilidad": float(x.std() * math.sqrt(252)),
            "sharpe": float(ex.mean() / ex.std() * math.sqrt(252)) if ex.std() > 0 else 0.0,
            "peor_caida": float((v / v.cummax() - 1).min()),
            "por_anio": {int(a): float((1 + g).prod() - 1) for a, g in x.groupby(x.index.year)}}


def sharpe_deflactado(r: pd.Series, letra: pd.Series, n_pruebas: int, sharpes: list[float]) -> float:
    """Bailey y López de Prado (2014): probabilidad de que el Sharpe sea real dado cuántas variantes se probaron."""
    from statistics import NormalDist
    ex = (r - letra).dropna(); ex = ex[ex != 0]
    sr = ex.mean() / ex.std()                                      # por día
    var_sr = np.var(np.array(sharpes) / math.sqrt(252))
    g = 0.5772156649; N = NormalDist()
    sr0 = math.sqrt(var_sr) * ((1 - g) * N.inv_cdf(1 - 1 / n_pruebas) + g * N.inv_cdf(1 - 1 / (n_pruebas * math.e)))
    sk, ku = float(ex.skew()), float(ex.kurt()) + 3
    z = (sr - sr0) * math.sqrt(len(ex) - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    return N.cdf(z)


def main() -> None:
    m = Motor()
    fechas = pd.DatetimeIndex(m.fechas.astype("datetime64[ns]"))
    letra = pd.read_csv(ROOT / "data" / "rates" / "DTB3.csv", parse_dates=["date"]).set_index("date").value
    letra_diaria = (pd.to_numeric(letra, errors="coerce").reindex(fechas, method="ffill").fillna(0) / 100) / 252
    p = precios(fechas)
    rA, rotA = tendencia(p, letra_diaria)
    rA2, rotA2 = tendencia(p, letra_diaria, cortos=True)
    rB, rotB = factores(m, fechas, letra_diaria)
    rC, rotC = (rA + rB) / 2, (rotA + rotB) / 2
    rC2, rotC2 = (rA2 + rB) / 2, (rotA2 + rotB) / 2
    spy = p.SPY.pct_change(fill_method=None).fillna(0.0)
    sesenta = 0.6 * spy + 0.4 * p.IEF.pct_change(fill_method=None).fillna(0.0)
    series = {"Índice S&P 500 (SPY)": spy, "Cartera clásica 60/40": sesenta}
    estrategias = set()
    for nombre, r, rot in (("A tendencia multimercado", rA, rotA), ("A2 tendencia con cortos", rA2, rotA2),
                           ("B acciones multifactor", rB, rotB), ("C = A + B", rC, rotC), ("C2 = A2 + B", rC2, rotC2)):
        for obj in OBJETIVOS:
            series[f"{nombre} · vol {obj:.0%}"] = controla_volatilidad(r, rot, obj, letra_diaria)
            estrategias.add(f"{nombre} · vol {obj:.0%}")
    inicio = "2005-01-01"
    res = {}
    sharpes = [metricas(s, letra_diaria, inicio, "2021-12-31")["sharpe"] for k, s in series.items() if k in estrategias]
    for k, s in series.items():
        res[k] = {"2005-2021": metricas(s, letra_diaria, inicio, "2021-12-31"),
                  "2022-2026": metricas(s, letra_diaria, "2022-01-01", "2026-12-31")}
        if k in estrategias:
            res[k]["prob_sharpe_real"] = sharpe_deflactado(s.loc[inicio:"2021-12-31"], letra_diaria.loc[inicio:"2021-12-31"],
                                                           len(sharpes), sharpes)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "clones.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{'estrategia':42s} {'2005-21 %/año':>13s} {'vol':>6s} {'Sharpe':>6s} {'peor caída':>10s} | {'2022-26 %/año':>13s} {'peor caída':>10s} | prob. real")
    for k, v in res.items():
        a, b = v["2005-2021"], v["2022-2026"]
        pr = f"{v['prob_sharpe_real']:.0%}" if "prob_sharpe_real" in v else "-"
        print(f"{k:42s} {a['anual']:>13.1%} {a['volatilidad']:>6.0%} {a['sharpe']:>6.2f} {a['peor_caida']:>10.0%} | {b['anual']:>13.1%} {b['peor_caida']:>10.0%} | {pr}")


if __name__ == "__main__":
    main()
