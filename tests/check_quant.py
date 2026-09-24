"""G1 (fase 14): la caja cuantitativa da los números correctos y nunca mira el futuro.

Cada indicador se contrasta con una implementación INDEPENDIENTE (pandas/numpy) sobre
el histórico real de AAPL; Black-Scholes con el valor de libro (Hull) y la paridad
put-call; las griegas con diferencias finitas del propio precio. Y el endpoint de FMP
solo sirve barras <= hoy virtual: un día después, las filas antiguas no cambian."""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from _common import LiveApp, check, make_market_world
from econosim.market import quant
from econosim.market.data import MarketData
from econosim.twins.fundamentals_api import FundamentalsTwin

md = MarketData()
s = md.series["AAPL"]
bars = s.bars[3000:3500]
C = [b.close for b in bars]; H = [b.high for b in bars]; L = [b.low for b in bars]; V = [b.volume for b in bars]
c = pd.Series(C)

# --- medias ---------------------------------------------------------------------
check(abs(quant.sma(C, 20) - c.rolling(20).mean().iloc[-1]) < 1e-9, "SMA")
check(abs(quant.ema(C, 12) - c.ewm(span=12, adjust=False).mean().iloc[-1]) / C[-1] < 1e-6, "EMA")

# --- RSI de Wilder: pandas ewm(alpha=1/n) sobre ganancias/pérdidas ---------------
d = c.diff().dropna()
ag = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
al = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
rsi_ref = 100 - 100 / (1 + ag / al)
check(abs(quant.rsi(C, 14) - rsi_ref) < 0.05, f"RSI {quant.rsi(C, 14)} vs {rsi_ref}")
check(quant.rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], 14) == 100.0, "RSI solo subidas = 100")

# --- MACD -------------------------------------------------------------------------
m = quant.macd(C)
line = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
sig = line.ewm(span=9, adjust=False).mean()
check(abs(m["macd"] - line.iloc[-1]) / C[-1] < 1e-4 and abs(m["signal"] - sig.iloc[-1]) / C[-1] < 1e-3, f"MACD {m}")

# --- Bollinger (desviación poblacional, como la definición original) ---------------
b = quant.bollinger(C)
mid, sd = c.rolling(20).mean().iloc[-1], c.rolling(20).std(ddof=0).iloc[-1]
check(abs(b["upper"] - (mid + 2 * sd)) < 1e-9 and abs(b["lower"] - (mid - 2 * sd)) < 1e-9, "Bollinger")

# --- volatilidad realizada, beta, Sharpe, drawdown ---------------------------------
lr = np.diff(np.log(np.array(C)))
check(abs(quant.realized_vol(C, 20) - lr[-20:].std(ddof=1) * math.sqrt(252)) < 1e-12, "vol realizada")
spy = [md.series["SPY"].asof(x.day).close for x in bars]
lb = np.diff(np.log(np.array(spy)))
k = 252
beta_ref = np.cov(lr[-k:], lb[-k:], ddof=1)[0, 1] / np.var(lb[-k:], ddof=1)
bc = quant.beta_corr(C, spy)
check(abs(bc["beta"] - beta_ref) < 1e-9 and abs(bc["corr"] - np.corrcoef(lr[-k:], lb[-k:])[0, 1]) < 1e-9, "beta/corr")
check(abs(quant.max_drawdown([100, 120, 90, 130, 104]) - (-0.25)) < 1e-12, "max drawdown")
w = np.array(C[-253:]); r = np.diff(np.log(w))
check(abs(quant.sharpe(C) - r.mean() * 252 / (r.std(ddof=1) * math.sqrt(252))) < 1e-9, "Sharpe")

# --- ATR / ADX / Williams: rangos válidos y ATR contra pandas ----------------------
h, l_, cc = pd.Series(H), pd.Series(L), c
tr = pd.concat([h - l_, (h - cc.shift()).abs(), (l_ - cc.shift()).abs()], axis=1).max(axis=1).iloc[1:]
atr_ref = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
check(abs(quant.atr(H, L, C) - atr_ref) / atr_ref < 0.01, f"ATR {quant.atr(H, L, C)} vs {atr_ref}")
check(0 <= quant.adx(H, L, C) <= 100 and -100 <= quant.williams_r(H, L, C) <= 0, "ADX/Williams fuera de rango")

# --- Black-Scholes: valor de libro (Hull, S=K=100, T=1, r=5 %, σ=20 %) --------------
call, put = quant.bs("call", 100, 100, 1, 0.05, 0.2), quant.bs("put", 100, 100, 1, 0.05, 0.2)
check(abs(call["price"] - 10.4506) < 1e-3 and abs(put["price"] - 5.5735) < 1e-3, f"BS {call['price']} {put['price']}")
check(abs(call["price"] - put["price"] - (100 - 100 * math.exp(-0.05))) < 1e-9, "paridad put-call")
# griegas = derivadas del precio (diferencias finitas centradas)
S, K, T, r_, sg, e = 103.0, 100.0, 0.25, 0.03, 0.35, 1e-3
for kind in ("call", "put"):
    g = quant.bs(kind, S, K, T, r_, sg)
    P = lambda **kw: quant.bs(kind, kw.get("S", S), K, kw.get("T", T), kw.get("r", r_), kw.get("sg", sg))["price"]
    check(abs(g["delta"] - (P(S=S + e) - P(S=S - e)) / (2 * e)) < 1e-5, f"delta {kind}")
    check(abs(g["gamma"] - (P(S=S + e) - 2 * P() + P(S=S - e)) / e ** 2) < 1e-3, f"gamma {kind}")
    check(abs(g["vega"] - (P(sg=sg + e) - P(sg=sg - e)) / (2 * e) / 100) < 1e-5, f"vega {kind}")
    check(abs(g["rho"] - (P(r=r_ + e) - P(r=r_ - e)) / (2 * e) / 100) < 1e-5, f"rho {kind}")
    check(abs(g["theta"] - (-(P(T=T + e) - P(T=T - e)) / (2 * e) / 365)) < 1e-5, f"theta {kind}")
check(quant.bs("call", 100, 100, 1, 0.05, 0.2)["gamma"] > 0, "gamma positiva")

# --- insumos macro reales ----------------------------------------------------------
mc = quant.macro()
from datetime import date
check(abs(mc.risk_free(date(2008, 9, 2)) - 0.0172) < 0.003, f"T-bill 2008-09 {mc.risk_free(date(2008, 9, 2))}")
check(mc.risk_free(date(2021, 6, 1)) < 0.001, "T-bill 2021 casi cero")
check(mc.vix.asof(date(2008, 11, 20)) > 70, "VIX de nov-2008 real")

# --- sin futuro: indicadores sobre el histórico truncado no cambian al añadir días --
ind_a = quant.indicators(H[:400], L[:400], C[:400], V[:400], spy[:400])
ind_b = quant.indicators(H[:400], L[:400], C[:400], V[:400], spy[:400])
check(ind_a == ind_b, "indicadores no deterministas")
check(quant.indicators(H[:401], L[:401], C[:401], V[:401], spy[:401])["price"] == C[400], "el último precio es el de hoy")

# --- endpoint real /api/v3/technical_indicator del gemelo de FMP --------------------
import requests
w, md2, mask, a = make_market_world(seed="quant", years=2, initial_eur=1000.0)
fm = FundamentalsTwin(w, md2, mask, api_key="k")
alias = mask.aliases[0]
real = mask.to_real(alias)
with LiveApp(fm.app()) as app:
    U = app.url
    r1 = requests.get(U(f"/api/v3/technical_indicator/1day/{alias}"), params={"type": "rsi", "period": 14, "apikey": "k"}).json()
    check(len(r1) == 100 and "rsi" in r1[0], f"filas rsi {len(r1)}")
    hoy_disp = w.clock.display(w.clock.real_now()).date().isoformat()
    check(r1[0]["date"][:10] <= hoy_disp, f"fila del futuro {r1[0]['date']} > {hoy_disp}")
    check(str(mask.start_day.year) not in r1[0]["date"] and real not in str(r1), "fuga de año o símbolo real")
    check(abs(r1[0]["close"] - 100.0) < 1.0, "precio enmascarado (≈100 al arranque)")
    # un mes después, la fila de hoy sigue valiendo lo mismo y aparecen filas nuevas
    w.advance(timedelta(days=30))
    r2 = requests.get(U(f"/api/v3/technical_indicator/1day/{alias}"), params={"type": "rsi", "period": 14, "apikey": "k"}).json()
    viejo = {x["date"]: x["rsi"] for x in r2}
    check(r1[0]["date"] in viejo and abs(viejo[r1[0]["date"]] - r1[0]["rsi"]) < 1e-9, "una fila pasada cambió")
    check(r2[0]["date"] > r1[0]["date"], "no aparecen días nuevos")
    for t in ("sma", "ema", "wma", "williams", "adx", "standardDeviation"):
        rr = requests.get(U(f"/api/v3/technical_indicator/1day/{alias}"), params={"type": t, "period": 10, "apikey": "k"}).json()
        check(rr and t in rr[0], f"tipo {t}")
    bad = requests.get(U(f"/api/v3/technical_indicator/1day/{alias}"), params={"type": "gamma", "apikey": "k"})
    check(bad.status_code == 400, "tipo inválido debe dar 400")

print("QUANT OK")
