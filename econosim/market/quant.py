"""Caja de herramientas cuantitativa: indicadores técnicos, riesgo y opciones (griegas).

Todo se calcula SOLO con barras <= el día pedido (nada del futuro) y es puro: mismas
barras -> mismo número. Lo usan el gemelo de datos (lo que la IA puede consultar por
API) y el cerebro Laya (lo que ve en su estado).

Opciones: no existe un histórico gratuito de cadenas de opciones reales, así que el
precio y las griegas salen de Black-Scholes con insumos REALES de cada día:
  * tipo sin riesgo = letra del Tesoro a 3 meses (FRED DTB3) de ese día;
  * volatilidad implícita = volatilidad realizada de 60 sesiones de la acción,
    escalada por la prima de riesgo de varianza real del mercado ese día
    (VIX / volatilidad realizada de 30 sesiones del S&P 500), acotada a [0,8; 2,0].
Es una ESTIMACIÓN DE MODELO y se presenta como tal (igual que el consenso de
fundamentales): inventar un smile o un sesgo enseñaría una señal que fuera no existe.
"""
from __future__ import annotations

import csv
import math
from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent.parent
RATES_DIR = ROOT / "data" / "rates"
TRADING_DAYS = 252


# ============================================================ series básicas
def sma(x: Sequence[float], n: int) -> Optional[float]:
    return sum(x[-n:]) / n if len(x) >= n else None


def ema_series(x: Sequence[float], n: int) -> list[float]:
    """EMA estándar (alfa = 2/(n+1)) sembrada con la SMA de las n primeras."""
    if len(x) < n:
        return []
    a = 2.0 / (n + 1)
    out = [sum(x[:n]) / n]
    for v in x[n:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def ema(x: Sequence[float], n: int) -> Optional[float]:
    s = ema_series(x, n)
    return s[-1] if s else None


def rsi_series(close: Sequence[float], n: int = 14) -> list[Optional[float]]:
    """RSI de Wilder (suavizado 1/n) para cada día; None hasta tener n+1 cierres.
    Recursivo: su valor depende del arranque, así que se calcula siempre desde el
    principio de la serie (una misma fecha da siempre el mismo número)."""
    out: list[Optional[float]] = [None] * len(close)
    if len(close) < n + 1:
        return out
    gains = [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses = [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    f = lambda: 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    out[n] = f()
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = f()
    return out


def rsi(close: Sequence[float], n: int = 14) -> Optional[float]:
    return rsi_series(close, n)[-1] if close else None


def macd(close: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    if len(close) < slow + signal:
        return None
    ef, es = ema_series(close, fast), ema_series(close, slow)
    line = [f - s for f, s in zip(ef[slow - fast:], es)]
    sig = ema_series(line, signal)
    return {"macd": line[-1], "signal": sig[-1], "hist": line[-1] - sig[-1]}


def bollinger(close: Sequence[float], n: int = 20, k: float = 2.0) -> Optional[dict]:
    if len(close) < n:
        return None
    w = close[-n:]
    m = sum(w) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    up, lo = m + k * sd, m - k * sd
    return {"mid": m, "upper": up, "lower": lo,
            "pct_b": (close[-1] - lo) / (up - lo) if up > lo else 0.5,
            "width": (up - lo) / m if m else 0.0}


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> Optional[float]:
    if len(close) < n + 1:
        return None
    tr = [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
          for i in range(1, len(close))]
    a = sum(tr[:n]) / n
    for t in tr[n:]:
        a = (a * (n - 1) + t) / n
    return a


def log_returns(close: Sequence[float]) -> list[float]:
    return [math.log(close[i] / close[i - 1]) for i in range(1, len(close)) if close[i - 1] > 0 and close[i] > 0]


def realized_vol(close: Sequence[float], n: int) -> Optional[float]:
    """Volatilidad realizada anualizada (desviación de log-retornos, n sesiones)."""
    r = log_returns(close[-(n + 1):])
    if len(r) < max(2, n - 1):
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((v - m) ** 2 for v in r) / (len(r) - 1)) * math.sqrt(TRADING_DAYS)


def momentum(close: Sequence[float], n: int) -> Optional[float]:
    return close[-1] / close[-1 - n] - 1 if len(close) > n and close[-1 - n] > 0 else None


def max_drawdown(close: Sequence[float]) -> Optional[float]:
    if len(close) < 2:
        return None
    peak, mdd = close[0], 0.0
    for v in close:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def sharpe(close: Sequence[float], n: int = TRADING_DAYS, rf: float = 0.0) -> Optional[float]:
    r = log_returns(close[-(n + 1):])
    if len(r) < 20:
        return None
    m = sum(r) / len(r)
    sd = math.sqrt(sum((v - m) ** 2 for v in r) / (len(r) - 1))
    return (m * TRADING_DAYS - rf) / (sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None


def beta_corr(close: Sequence[float], bench: Sequence[float], n: int = TRADING_DAYS) -> Optional[dict]:
    """Beta y correlación frente a un índice de referencia, con retornos alineados."""
    k = min(len(close), len(bench), n + 1)
    a, b = log_returns(close[-k:]), log_returns(bench[-k:])
    k = min(len(a), len(b))
    if k < 20:
        return None
    a, b = a[-k:], b[-k:]
    ma, mb = sum(a) / k, sum(b) / k
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (k - 1)
    va = sum((x - ma) ** 2 for x in a) / (k - 1)
    vb = sum((y - mb) ** 2 for y in b) / (k - 1)
    if va <= 0 or vb <= 0:
        return None
    return {"beta": cov / vb, "corr": cov / math.sqrt(va * vb)}


def williams_r(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> Optional[float]:
    if len(close) < n:
        return None
    hh, ll = max(high[-n:]), min(low[-n:])
    return -100.0 * (hh - close[-1]) / (hh - ll) if hh > ll else -50.0


def adx_series(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> list[Optional[float]]:
    """ADX de Wilder (fuerza de la tendencia, 0-100) para cada día, desde el principio."""
    out: list[Optional[float]] = [None] * len(close)
    if len(close) < 2 * n + 1:
        return out
    pdm, mdm, tr = [], [], []
    for i in range(1, len(close)):
        up, dn = high[i] - high[i - 1], low[i - 1] - low[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    s_tr, s_p, s_m = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dx = []
    for i in range(n, len(tr) + 1):
        if i > n:
            s_tr = s_tr - s_tr / n + tr[i - 1]
            s_p = s_p - s_p / n + pdm[i - 1]
            s_m = s_m - s_m / n + mdm[i - 1]
        pdi = 100 * s_p / s_tr if s_tr else 0.0
        mdi = 100 * s_m / s_tr if s_tr else 0.0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    # dx[k] corresponde al día k+n; el primer ADX (media de n dx) al día 2n-1
    a = sum(dx[:n]) / n
    out[2 * n - 1] = a
    for k in range(n, len(dx)):
        a = (a * (n - 1) + dx[k]) / n
        out[k + n] = a
    return out


def adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> Optional[float]:
    return adx_series(high, low, close, n)[-1] if close else None


def zscore_last(x: Sequence[float], n: int = 20) -> Optional[float]:
    if len(x) < n + 1:
        return None
    w = x[-n - 1:-1]
    m = sum(w) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return (x[-1] - m) / sd if sd > 0 else 0.0


# ============================================================ Black-Scholes
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs(kind: str, S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> dict:
    """Precio y griegas de Black-Scholes-Merton (europea).

    kind: "call" | "put". T en años. theta POR DÍA natural (convención de los brókers),
    vega y rho por 1 punto porcentual (0,01)."""
    if T <= 0 or sigma <= 0:
        intr = max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
        itm = intr > 0
        return {"price": intr, "delta": (1.0 if kind == "call" else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    dq, dr = math.exp(-q * T), math.exp(-r * T)
    gamma = dq * _npdf(d1) / (S * sq)
    vega = S * dq * _npdf(d1) * math.sqrt(T) / 100.0
    if kind == "call":
        price = S * dq * _ncdf(d1) - K * dr * _ncdf(d2)
        delta = dq * _ncdf(d1)
        theta = (-S * dq * _npdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * dr * _ncdf(d2)
                 + q * S * dq * _ncdf(d1)) / 365.0
        rho = K * T * dr * _ncdf(d2) / 100.0
    else:
        price = K * dr * _ncdf(-d2) - S * dq * _ncdf(-d1)
        delta = -dq * _ncdf(-d1)
        theta = (-S * dq * _npdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * dr * _ncdf(-d2)
                 - q * S * dq * _ncdf(-d1)) / 365.0
        rho = -K * T * dr * _ncdf(-d2) / 100.0
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


# ============================================================ series macro reales
class _Daily:
    def __init__(self, path: Path):
        self.days: list[date] = []
        self.vals: list[float] = []
        if path.exists():
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    self.days.append(datetime.strptime(row["date"], "%Y-%m-%d").date())
                    self.vals.append(float(row["value"]))

    def asof(self, d: date) -> Optional[float]:
        i = bisect_right(self.days, d)
        return self.vals[i - 1] if i else None


class Macro:
    """Tipo sin riesgo y VIX reales por día (solo el motor los ve; no se sirven crudos)."""

    def __init__(self, rates_dir: Path = RATES_DIR):
        self.tbill = _Daily(rates_dir / "DTB3.csv")
        self.vix = _Daily(rates_dir / "VIXCLS.csv")

    def risk_free(self, d: date) -> float:
        v = self.tbill.asof(d)
        return (v if v is not None else 2.0) / 100.0

    def vrp_ratio(self, d: date, spx_close: Sequence[float]) -> float:
        """VIX / volatilidad realizada 30d del S&P 500 de ese día, acotada a [0,8; 2,0]."""
        v = self.vix.asof(d)
        rv = realized_vol(spx_close, 30)
        if v is None or not rv:
            return 1.2                       # mediana histórica aproximada de la prima
        return min(2.0, max(0.8, (v / 100.0) / rv))


_MACRO: list = []


def macro() -> Macro:
    if not _MACRO:
        _MACRO.append(Macro())
    return _MACRO[0]


def implied_vol(close: Sequence[float], spx_close: Sequence[float], d: date) -> Optional[float]:
    rv = realized_vol(close, 60) or realized_vol(close, 20)
    if not rv:
        return None
    return rv * macro().vrp_ratio(d, spx_close)


# ============================================================ resumen completo
def indicators(high: Sequence[float], low: Sequence[float], close: Sequence[float],
               volume: Sequence[float], bench: Optional[Sequence[float]] = None) -> dict:
    """Todos los indicadores del último día de las series (que ya terminan en 'hoy')."""
    out: dict = {"price": close[-1] if close else None}
    for n in (5, 20, 60, 120, 250):
        out[f"mom_{n}"] = momentum(close, n)
    out["sma_20"], out["sma_50"], out["sma_200"] = sma(close, 20), sma(close, 50), sma(close, 200)
    out["ema_12"], out["ema_26"] = ema(close, 12), ema(close, 26)
    out["rsi_14"] = rsi(close, 14)
    out["macd"] = macd(close)
    out["bollinger"] = bollinger(close)
    a = atr(high, low, close, 14)
    out["atr_14"] = a
    out["atr_pct"] = a / close[-1] if a and close[-1] else None
    out["adx_14"] = adx(high, low, close, 14)
    out["williams_r_14"] = williams_r(high, low, close, 14)
    out["vol_20"], out["vol_60"] = realized_vol(close, 20), realized_vol(close, 60)
    w = close[-TRADING_DAYS:]
    out["high_52w"], out["low_52w"] = (max(w), min(w)) if w else (None, None)
    out["from_high_52w"] = close[-1] / out["high_52w"] - 1 if w else None
    out["max_drawdown_1y"] = max_drawdown(w)
    out["sharpe_1y"] = sharpe(close)
    out["volume_z_20"] = zscore_last(volume, 20)
    bc = beta_corr(close, bench) if bench else None
    out["beta_1y"] = bc["beta"] if bc else None
    out["corr_1y"] = bc["corr"] if bc else None
    return out


def option_chain(S: float, close: Sequence[float], spx_close: Sequence[float], d: date,
                 days_list: Sequence[int] = (30, 60, 90),
                 moneyness: Sequence[float] = (0.9, 0.95, 1.0, 1.05, 1.1)) -> Optional[dict]:
    """Cadena modelo: para cada vencimiento y strike, call y put con precio y griegas."""
    iv = implied_vol(close, spx_close, d)
    if not iv or S <= 0:
        return None
    r = macro().risk_free(d)
    rows = []
    for days in days_list:
        T = days / 365.0
        for m in moneyness:
            K = round(S * m, 2)
            for kind in ("call", "put"):
                g = bs(kind, S, K, T, r, iv)
                rows.append({"days": days, "strike": K, "type": kind, **g})
    return {"iv": iv, "risk_free": r, "rows": rows}
