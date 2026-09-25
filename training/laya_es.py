"""Entrenamiento POR GENERACIONES de verdad: evolución de Laya con Estrategias Evolutivas.

Referencias (lo que se implementa aquí, no una imitación):
  * Salimans et al., "Evolution Strategies as a Scalable Alternative to RL" (OpenAI, 2017):
    población de perturbaciones gaussianas de los pesos, muestreo en espejo (+ε/−ε),
    aptitud transformada a rangos centrados, actualización con Adam, semillas para
    regenerar el ruido sin guardarlo.
  * Qiu et al., "Evolution Strategies at Scale: LLM Fine-Tuning Beyond RL" (2025):
    población de 30 basta incluso en modelos grandes; sin retropropagación.
  * Neuroevolución para bolsa (NEAT multi-indicador, 2025; GP maximizando Sharpe, Liu-Zhou-Zhu):
    aptitud = capital final ajustado por riesgo, todos los individuos sobre la MISMA
    muestra de mercado, costes de operar dentro de la aptitud, y validación fuera de
    muestra (la campeona se elige en 2019-21 y se informa en 2022-25, que no decide nada).

Qué evoluciona: la cabeza de puntuación de Laya (`scorer`, ~1 M parámetros), que convierte
la representación de cada opción (vender / mantener / comprar) en su nota. El codificador
y las capas de cabeza anteriores están congelados, así que la representación de cada
estado se calcula UNA vez y se guarda en disco (training/laya_cache). Eso hace posible
lo "acelerado": cada individuo solo recalcula ~1 M de parámetros y juega sus vidas en un
simulador rápido con las mismas reglas del mundo (comisión y tasas del bróker, horquilla,
factura diaria de Hetzner con tope mensual e impago = muerte, mandato, tesorería). La
campeona se comprueba además en el MUNDO REAL de ECONOSIM (ledger y gemelos).

El estado que ve Laya aquí no lleva el nombre enmascarado, ni el día de vida, ni la caja:
solo el mercado de ese valor y si lo tiene o no ("position held/none"). Así la
representación no depende de la política y se puede reutilizar entre individuos.

Uso:  training/.venv/Scripts/python.exe training/laya_es.py            (hasta que se pare)
      Parar limpio: crear STOP en la carpeta de la ejecución, o PARAR en el panel.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as Fn

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from laya_brain import ACTIONS, LayaPolicy                                          # noqa: E402
from laya_life import (MIN_TICKET_EUR, RESERVE_DAYS, TREASURY_DAYS, Fundamentals,   # noqa: E402
                       LayaLife, MarketData)
from econosim.ledger import to_cents                                               # noqa: E402
from econosim.twins.alpaca import (COMMISSION_MAX_PCT, COMMISSION_MIN,             # noqa: E402
                                   COMMISSION_PER_SHARE, SEC_FEE_RATE, SPREAD_BPS,
                                   TAF_MAX, TAF_PER_SHARE)
from econosim.twins.hetzner import GRACE_DAYS, IPV4_HOURLY                          # noqa: E402

RUNS = HERE / "laya_runs"
CACHE = HERE / "laya_cache"
BUY, HOLD, SELL = ACTIONS.index("buy"), ACTIONS.index("hold"), ACTIONS.index("sell")
SPLITS = {   # arranques de vida permitidos; las vidas duran 126 sesiones (~6 meses)
    "train": (date(2010, 1, 4), date(2017, 12, 29)),
    "val": (date(2019, 3, 1), date(2021, 12, 31)),
    "test": (date(2022, 8, 1), date(2025, 12, 31)),
    "all": (date(2005, 3, 1), date(2025, 12, 31)),       # walk-forward: toda la historia
}
DECIDE_EVERY = 5
TRAIN_DEFAULT = SPLITS["train"]


ORIGINAL_UNIVERSE = 25


def cache_tag(split: str, n_symbols: int = ORIGINAL_UNIVERSE) -> str:
    """Nombre de caché: tramo y universo por defecto conservan el nombre de siempre; si cambian,
    van en el nombre (una caché de otro universo NO se reutiliza)."""
    lo, hi = SPLITS[split]
    t = "" if (split != "train" or SPLITS[split] == TRAIN_DEFAULT) else f"_{lo:%Y%m%d}"
    return t + ("" if n_symbols == ORIGINAL_UNIVERSE else f"_u{n_symbols}")


def log(run: Path, msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(run / "log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================================ calendario
class Grid:
    """Sesiones del mercado (las del índice) y la rejilla de días de decisión (cada 5)."""

    def __init__(self, md: MarketData):
        self.days = list(md.series["SPY"]._days)
        self.idx = {d: i for i, d in enumerate(self.days)}

    def starts(self, split: str) -> list[int]:
        """Arranques en la rejilla común (cada 5 sesiones): así todas las vidas deciden los
        MISMOS días y la caché es 5 veces más pequeña sin perder información."""
        lo, hi = SPLITS[split]
        return [i for i, d in enumerate(self.days) if lo <= d <= hi and i % DECIDE_EVERY == 0]

    def decision_days(self, split: str, sessions: int) -> list[date]:
        out = set()
        for i in self.starts(split):
            for n in range(0, sessions, DECIDE_EVERY):
                if i + n < len(self.days):
                    out.add(self.days[i + n])
        return sorted(out)


# ============================================================================ caché de estados
def state_texts(md: MarketData, fu: Fundamentals, d: date) -> dict:
    """{símbolo real: texto base} del día d, con las MISMAS funciones que ve Laya en el mundo
    (LayaLife.state_text), quitando alias, día de vida y caja."""
    L = LayaLife(md, fu, "cache", d, sessions=1)
    if L.mask.start_day != d:
        return {}
    market = L.market_summary(d)
    out = {}
    for alias in L.mask.aliases:
        st = L.state_text(alias, d, 0, market)
        if st:
            lines = st.split("\n")
            out[L.mask.to_real(alias)] = "\n".join(lines[1:-2])      # fuera "alias day n", posición y caja
    return out


@torch.inference_mode()
def marker_vectors(pol: LayaPolicy, texts: list[str], batch: int = 32) -> np.ndarray:
    """Representación de las 3 opciones justo ANTES del scorer (lo único que evoluciona)."""
    m = pol.model
    out = []
    for i in range(0, len(texts), batch):
        b = pol.encode(texts[i: i + batch])
        dev = pol.device
        ids, att = b["input_ids"].to(dev), b["attention_mask"].to(dev)
        with torch.autocast(device_type=dev.type, dtype=torch.bfloat16, enabled=dev.type == "cuda"):
            h = m.encoder(input_ids=ids, attention_mask=att).last_hidden_state
            h = h + m.type_emb(b["qtype"].to(dev))[:, None, :]
            pad = ~att.bool()
            for layer in m.head.layers:
                h = layer(h, src_key_padding_mask=pad)
            pos = b["marker_pos"].to(dev).clamp(min=0)[:, : len(ACTIONS)]
            v = torch.gather(h, 1, pos[:, :, None].expand(-1, -1, h.size(-1)))
        out.append(v.float().cpu().numpy().astype(np.float16))
    return np.concatenate(out)


def build_cache(pol, md, fu, grid: Grid, split: str, sessions: int, on_progress=None) -> dict:
    path = CACHE / f"{split}_{sessions}{cache_tag(split, len(md.symbols))}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        keys = [tuple(k.split("|")) for k in z["keys"]]
        return {"M": z["M"], "index": {(s, d, h): i for i, (s, d, h) in enumerate(keys)}}
    CACHE.mkdir(parents=True, exist_ok=True)
    days = grid.decision_days(split, sessions)
    keys, texts = [], []
    for j, d in enumerate(days):
        for sym, base in state_texts(md, fu, d).items():
            for held in ("0", "1"):
                keys.append((sym, d.isoformat(), held))
                texts.append(base + ("\nposition held" if held == "1" else "\nposition none"))
        if on_progress and j % 20 == 0:
            on_progress(f"caché {split}: textos {j}/{len(days)} días")
    M = np.zeros((len(texts), len(ACTIONS), pol.model.encoder.config.hidden_size), dtype=np.float16)
    step = 512
    for i in range(0, len(texts), step):
        M[i: i + step] = marker_vectors(pol, texts[i: i + step])
        if on_progress:
            on_progress(f"caché {split}: {min(i + step, len(texts))}/{len(texts)} estados por Laya")
    np.savez(path, M=M, keys=np.array(["|".join(k) for k in keys]))
    return {"M": M, "index": {k: i for i, k in enumerate(keys)}}


# ============================================================================ cerebro B: los mismos números, sin texto
# Para comparar con Laya de forma justa, B ve EXACTAMENTE la misma información: se sacan los
# números del mismo texto de estado (mismas funciones, mismos redondeos). Lo único que
# cambia es que B los recibe como números y Laya como texto.
def parse_state(text: str) -> dict:
    out = {}
    for line in text.split("\n"):
        tok = line.replace(",", " ").split()
        if not tok:
            continue
        if tok[0] == "position":
            out["held"] = 1.0 if tok[1] == "held" else 0.0
            continue
        if tok[0] == "fund":
            out["no_accounts"] = 1.0
            continue
        pref = ""
        if tok[0] in ("mom", "market", "opt30"):
            pref, tok = tok[0] + "_", tok[1:]
        for k in range(0, len(tok) - 1, 2):
            v = tok[k + 1].rstrip("d")
            try:
                out[pref + tok[k]] = float(v)
            except ValueError:
                out[pref + tok[k]] = float("nan")         # "na": se marca aparte
    return out


def build_num_cache(md, fu, grid: Grid, split: str, sessions: int, on_progress=None) -> dict:
    path = CACHE / f"num_{split}_{sessions}{cache_tag(split, len(md.symbols))}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        keys = [tuple(k.split("|")) for k in z["keys"]]
        return {"M": z["M"], "names": list(z["names"]), "index": {k: i for i, k in enumerate(keys)}}
    CACHE.mkdir(parents=True, exist_ok=True)
    days = grid.decision_days(split, sessions)
    keys, rows = [], []
    for j, d in enumerate(days):
        for sym, base in state_texts(md, fu, d).items():
            for held in ("0", "1"):
                keys.append((sym, d.isoformat(), held))
                rows.append(parse_state(base + ("\nposition held" if held == "1" else "\nposition none")))
        if on_progress and j % 20 == 0:
            on_progress(f"caché numérica {split}: {j}/{len(days)} días")
    names = sorted({k for r in rows for k in r})
    M = np.array([[r.get(n, float("nan")) for n in names] for r in rows], dtype=np.float32)
    np.savez(path, M=M, names=np.array(names), keys=np.array(["|".join(k) for k in keys]))
    return {"M": M, "names": names, "index": {k: i for i, k in enumerate(keys)}}


def normalize_num(caches: dict) -> None:
    """Estandariza con la media y desviación del ENTRENAMIENTO (nada de validación/test),
    'na' -> 0 más una columna que marca el hueco. Mismas columnas en los tres tramos."""
    names = sorted(set().union(*[c["names"] for c in caches.values()]))
    for c in caches.values():
        col = {n: i for i, n in enumerate(c["names"])}
        c["M"] = np.stack([c["M"][:, col[n]] if n in col else np.full(len(c["M"]), np.nan, np.float32)
                           for n in names], 1)
        c["names"] = names
    tr = caches["train"]["M"]
    mu, sd = np.nanmean(tr, 0), np.nanstd(tr, 0) + 1e-6
    for c in caches.values():
        miss = np.isnan(c["M"]).astype(np.float32)
        z = np.clip(np.nan_to_num((c["M"] - mu) / sd, nan=0.0), -5, 5)
        keep = miss.any(0) if c is caches["train"] else None
        c["M"] = np.concatenate([z, miss], 1).astype(np.float32)


class MLPScorer:
    """Cerebro B: red pequeña (entradas -> 64 -> 3) sobre los números estandarizados."""

    def __init__(self, n_in: int, hidden: int = 64, device: str = "cpu", seed: int = 0):
        g = torch.Generator().manual_seed(seed)
        self.dev = torch.device(device)
        self.theta = [torch.randn(hidden, n_in, generator=g) / math.sqrt(n_in), torch.zeros(hidden),
                      torch.randn(len(ACTIONS), hidden, generator=g) / math.sqrt(hidden) * 0.1,
                      torch.zeros(len(ACTIONS))]
        self.theta = [t.to(self.dev) for t in self.theta]
        self.rms = [max(float(t.pow(2).mean().sqrt()), 0.05) for t in self.theta]

    def n_params(self) -> int:
        return sum(t.numel() for t in self.theta)

    @staticmethod
    def forward(theta, X):
        w1, b1, w2, b2 = theta
        return torch.tanh(X @ w1.T + b1) @ w2.T + b2

    def noise(self, seed: int):
        g = torch.Generator(device=self.dev).manual_seed(seed)
        return [torch.randn(t.shape, generator=g, device=self.dev) * r for t, r in zip(self.theta, self.rms)]

    def perturbed(self, eps, sign: float, sigma: float):
        return [t + sign * sigma * e for t, e in zip(self.theta, eps)]

    def probs(self, theta, X):
        with torch.inference_mode():
            return torch.softmax(self.forward(theta, X), -1).cpu().numpy()


# ============================================================================ cerebro C: inversor de factores
# No memoriza: analiza cada acción con las reglas mejor documentadas de la literatura y la
# evolución solo decide CUÁNTO pesa cada una (~16 números). Con tan pocos grados de libertad
# no puede aprenderse la historia; solo puede quedarse con lo que funciona en todas las épocas.
#   momentum 12-1 (Jegadeesh-Titman) · reversión a 1 mes · baja volatilidad · valor (beneficio/precio)
#   · calidad (ROE, margen) · tendencia (vs media de 200) · baja beta · crecimiento de ventas
#   · rentabilidad por flujo de caja · resultados inminentes · + régimen de mercado (índice vs su
#   media de 200): en tendencia bajista vende acciones y puede comprar el ETF inverso del índice.
FACTORS = ("momentum_12_1", "reversion_1m", "baja_volatilidad", "valor", "roe", "margen",
           "tendencia", "baja_beta", "crecimiento", "flujo_caja")
INVERSE = {"SH", "PSQ", "DOG", "RWM"}
INV_SPY = "SH"
ETF_ALWAYS = {"SPY", "QQQ", "DIA", "IWM"} | INVERSE


def build_factor_matrix(cache: dict) -> None:
    """Sustituye cache["M"] por: rangos transversales (por fecha, -0.5..0.5) de cada factor +
    [resultados<=7d, tiene_posición, es_inverso, es_inverso_del_índice, tendencia del índice %]."""
    col = {n: i for i, n in enumerate(cache["names"])}
    M = cache["M"]

    def v(r, n):
        return M[r, col[n]] if n in col else np.nan

    raw = np.full((len(M), len(FACTORS)), np.nan, dtype=np.float64)
    extra = np.zeros((len(M), 5), dtype=np.float64)
    por_fecha = {}
    for (sym, dk, held), r in cache["index"].items():
        por_fecha.setdefault(dk, []).append((sym, held, r))
        m1y, m20 = v(r, "mom_1y"), v(r, "mom_20d")
        pe = v(r, "PE")
        raw[r] = [(1 + m1y / 100) / (1 + m20 / 100) - 1 if not (np.isnan(m1y) or np.isnan(m20)) else np.nan,
                  -m20, -v(r, "vol60"), (1.0 / pe) if pe and pe > 0 else np.nan, v(r, "ROE"), v(r, "margin"),
                  v(r, "mom_vsSMA200"), -v(r, "beta"), v(r, "revG"), v(r, "FCFy")]
        ein = v(r, "earningsIn")
        extra[r, 0] = 1.0 if not np.isnan(ein) and ein <= 7 else 0.0
        extra[r, 1] = 1.0 if held == "1" else 0.0
        extra[r, 2] = 1.0 if sym in INVERSE else 0.0
        extra[r, 3] = 1.0 if sym == INV_SPY else 0.0
    ranks = np.zeros_like(raw)
    for dk, lst in por_fecha.items():
        spy = next((r for s_, h, r in lst if s_ == "SPY" and h == "0"), None)
        trend = v(spy, "mom_vsSMA200") if spy is not None else 0.0
        cands = [(s_, h, r) for s_, h, r in lst if s_ not in INVERSE]
        base = {s_: r for s_, h, r in cands if h == "0"}
        for k in range(len(FACTORS)):
            vals = [(s_, raw[r, k]) for s_, r in base.items() if not np.isnan(raw[r, k])]
            if len(vals) < 3:
                continue
            order = sorted(vals, key=lambda x: x[1])
            rk = {s_: i / (len(order) - 1) - 0.5 for i, (s_, _) in enumerate(order)}
            for s_, h, r in cands:
                ranks[r, k] = rk.get(s_, 0.0)
        for s_, h, r in lst:
            extra[r, 4] = 0.0 if np.isnan(trend) else trend
    cache["M"] = np.concatenate([ranks, extra], 1).astype(np.float32)
    cache["names"] = list(FACTORS) + ["resultados_7d", "tiene", "inverso", "inverso_indice", "tendencia_indice"]


class FactorBrain:
    """Genes: pesos de los factores (+ resultados inminentes y apego a lo que ya tiene),
    umbrales de compra/venta y régimen (umbral de tendencia del índice, usar el inverso)."""
    K = len(FACTORS)

    def __init__(self, device: str = "cpu"):
        self.dev = torch.device(device)
        # punto de partida "de libro": momentum, baja volatilidad, valor, calidad y tendencia
        w = [0.5, 0.0, 0.3, 0.2, 0.15, 0.15, 0.3, 0.0, 0.1, 0.1, -0.3, 0.2]
        self.theta = [torch.tensor(w, dtype=torch.float32),                      # pesos
                      torch.tensor([0.35, -0.05], dtype=torch.float32),           # umbral comprar, vender
                      torch.tensor([-3.0, -1.0], dtype=torch.float32)]            # régimen: % bajo media 200, inverso on/off
        self.theta = [t.to(self.dev) for t in self.theta]
        self.rms = [0.25, 0.2, 2.0]

    def n_params(self) -> int:
        return sum(t.numel() for t in self.theta)

    def logits(self, theta, X):
        w, th, rg = theta
        K = self.K
        score = X[:, :K] @ w[:K] + X[:, K] * w[K] + X[:, K + 1] * w[K + 1]
        buy, sell = score - th[0], th[1] - score
        hold = torch.zeros_like(score)
        bear = X[:, K + 4] < rg[0]
        inv, inv_spy = X[:, K + 2] > 0.5, X[:, K + 3] > 0.5
        big = torch.full_like(score, 5.0)
        # régimen bajista: fuera de acciones
        buy = torch.where(bear & ~inv, -big, buy)
        sell = torch.where(bear & ~inv, big, sell)
        # inversos: solo el del índice, solo en régimen bajista y si el gen lo activa
        usar = inv_spy & bear & (rg[1] > 0)
        buy = torch.where(inv, torch.where(usar, big, -big), buy)
        sell = torch.where(inv, torch.where(usar, -big, big), sell)
        return torch.stack([sell, hold, buy], -1)          # orden de ACTIONS: sell, hold, buy

    def forward(self, theta, X):
        return self.logits(theta, X)

    def noise(self, seed: int):
        g = torch.Generator(device=self.dev).manual_seed(seed)
        return [torch.randn(t.shape, generator=g, device=self.dev) * r for t, r in zip(self.theta, self.rms)]

    def perturbed(self, eps, sign: float, sigma: float):
        return [t + sign * sigma * e for t, e in zip(self.theta, eps)]

    def probs(self, theta, X):
        with torch.inference_mode():
            return torch.softmax(self.logits(theta, X), -1).cpu().numpy()

    def describe(self, theta) -> str:
        w, th, rg = [t.tolist() for t in theta]
        partes = [f"{n} {w[i]:+.2f}" for i, n in enumerate(FACTORS)]
        partes += [f"resultados_7d {w[10]:+.2f}", f"apego {w[11]:+.2f}", f"comprar>{th[0]:.2f}", f"vender<{th[1]:.2f}",
                   f"refugio si índice {rg[0]:+.1f}% vs media200", f"inverso {'SÍ' if rg[1] > 0 else 'no'}"]
        return " · ".join(partes)


# ============================================================================ BOT + IA
class HybridBrain:
    """El BOT pone la estrategia (núcleo en el S&P 500 + satélite con las N de más momentum 12-1)
    y la IA solo ajusta encima: tamaño del núcleo, correcciones al ranking con los otros factores y
    cuándo sacar el satélite en tendencia bajista. Con todo a cero ES el bot: la IA solo cambia algo
    si gana al bot en periodos que no ha visto."""
    K = len(FACTORS)
    N = 2                                            # apuestas del satélite

    def __init__(self, device: str = "cpu"):
        self.dev = torch.device(device)
        w = torch.zeros(self.K + 1)                  # corrección por factor (+ resultados inminentes); momentum va fijo
        self.theta = [w, torch.tensor([0.0]),        # núcleo: 0.6 + 0.3·sigmoid(x)  -> 0 = 75 %
                      torch.tensor([-50.0])]         # sacar satélite si índice < x % bajo su media200 (-50 = nunca)
        self.rms = [0.3, 1.0, 3.0]
        self.groups = None

    def set_cache(self, cache: dict) -> None:
        """Agrupa las filas por (fecha, ¿tiene?) para hacer el ranking top-N dentro de cada fecha."""
        keys = {i: k for k, i in cache["index"].items()}
        g = {}
        self.spy_rows, self.etf = set(), np.zeros(len(cache["M"]), dtype=bool)
        for i, (sym, dk, held) in keys.items():
            if sym in ("SPY", "QQQ", "DIA", "IWM") or sym in INVERSE:
                self.etf[i] = True
                if sym == "SPY":
                    self.spy_rows.add(i)
                continue
            g.setdefault(dk, {"0": [], "1": []})[held].append(i)
        # para cada fila con posición, su gemela sin posición (mismo símbolo y fecha) para el rango
        self.twin = {}
        for (sym, dk, held), i in cache["index"].items():
            if held == "1":
                self.twin[i] = cache["index"].get((sym, dk, "0"))
        self.groups = g
        self.spy_list = sorted(self.spy_rows)

    def core(self, theta) -> float:
        return float(0.6 + 0.3 * torch.sigmoid(theta[1][0]))

    def n_params(self) -> int:
        return sum(t.numel() for t in self.theta)

    def noise(self, seed: int):
        g = torch.Generator(device=self.dev).manual_seed(seed)
        return [torch.randn(t.shape, generator=g, device=self.dev) * r for t, r in zip(self.theta, self.rms)]

    def perturbed(self, eps, sign: float, sigma: float):
        return [t + sign * sigma * e for t, e in zip(self.theta, eps)]

    def probs(self, theta, X):
        X = X.cpu().numpy() if hasattr(X, "cpu") else X
        w = theta[0].cpu().numpy()
        # momentum 12-1 (columna 0) con peso fijo 1 + correcciones de la IA
        score = X[:, 0] + X[:, 1:self.K] @ w[1:self.K] + X[:, self.K] * w[self.K]
        bear = X[:, self.K + 4] < float(theta[2][0])
        P = np.tile(np.array([0.0, 1.0, 0.0], dtype=np.float32), (len(X), 1))      # por defecto mantener
        rank = np.full(len(X), 10 ** 6)
        for dk, gr in self.groups.items():
            base = gr["0"]
            if not base:
                continue
            order = np.array(base)[np.argsort(-score[base])]
            for r_, i in enumerate(order):
                rank[i] = r_
        for i, j in self.twin.items():
            if j is not None:
                rank[i] = rank[j]
        buy = (rank < self.N) & ~bear & ~self.etf
        sell = ((rank >= self.N * 3) | bear) & ~self.etf
        P[buy] = [0.0, 0.0, 1.0]
        P[sell] = [1.0, 0.0, 0.0]
        P[buy, 2] = 1.0 - rank[buy] * 0.01                                           # convicción por puesto
        etf_no_spy = self.etf.copy()
        etf_no_spy[self.spy_list] = False
        P[etf_no_spy] = [1.0, 0.0, 0.0]                                              # ni inversos ni otros ETF
        P[self.spy_list] = [0.0, 0.0, 9.0]                                           # el núcleo: comprar primero, no vender
        return P

    def describe(self, theta) -> str:
        w = theta[0].tolist()
        partes = [f"{n} {w[i]:+.2f}" for i, n in enumerate(FACTORS) if i > 0]
        return (f"núcleo S&P 500 {self.core(theta):.0%} · satélite momentum top{self.N} · correcciones: "
                + " · ".join(partes) + f" · resultados_7d {w[self.K]:+.2f} · sacar satélite si índice "
                + f"{float(theta[2][0]):+.1f}% vs media200")


# ============================================================================ la cabeza que evoluciona
class Scorer:
    """scorer de Laya en forma funcional: LayerNorm -> Linear -> GELU -> Linear."""

    NAMES = ("0.weight", "0.bias", "1.weight", "1.bias", "3.weight", "3.bias")

    def __init__(self, pol: LayaPolicy):
        sd = pol.model.scorer.state_dict()
        self.dev = pol.device
        self.theta = [sd[n].detach().float().clone().to(self.dev) for n in self.NAMES]
        # escala de ruido por tensor: una fracción de su propio tamaño típico
        self.rms = [max(float(t.pow(2).mean().sqrt()), 1e-3) for t in self.theta]

    def n_params(self) -> int:
        return sum(t.numel() for t in self.theta)

    @staticmethod
    def forward(theta, X: torch.Tensor) -> torch.Tensor:
        ln_w, ln_b, w1, b1, w2, b2 = theta
        x = Fn.layer_norm(X, (X.shape[-1],), ln_w, ln_b, 1e-5)
        x = Fn.gelu(x @ w1.T + b1)
        return (x @ w2.T + b2).squeeze(-1)

    def noise(self, seed: int):
        g = torch.Generator(device=self.dev).manual_seed(seed)
        return [torch.randn(t.shape, generator=g, device=self.dev) * r for t, r in zip(self.theta, self.rms)]

    def perturbed(self, eps, sign: float, sigma: float):
        return [t + sign * sigma * e for t, e in zip(self.theta, eps)]

    def probs(self, theta, X: torch.Tensor) -> np.ndarray:
        with torch.inference_mode():
            return torch.softmax(self.forward(theta, X), -1).cpu().numpy()

    def apply_to(self, pol: LayaPolicy, theta) -> None:
        sd = pol.model.scorer.state_dict()
        for n, t in zip(self.NAMES, theta):
            sd[n].copy_(t.to(sd[n].dtype))


class Adam:
    def __init__(self, shapes, lr: float, b1=0.9, b2=0.999):
        self.lr, self.b1, self.b2, self.t = lr, b1, b2, 0
        self.m = [torch.zeros(s) for s in shapes]
        self.v = [torch.zeros(s) for s in shapes]

    def step(self, grads):
        self.t += 1
        out = []
        for i, g in enumerate(grads):
            g = g.cpu()
            self.m[i] = self.b1 * self.m[i] + (1 - self.b1) * g
            self.v[i] = self.b2 * self.v[i] + (1 - self.b2) * g * g
            mh = self.m[i] / (1 - self.b1 ** self.t)
            vh = self.v[i] / (1 - self.b2 ** self.t)
            out.append(self.lr * mh / (vh.sqrt() + 1e-8))
        return out


# ============================================================================ simulador rápido
class FastSim:
    """Las reglas del mundo de ECONOSIM sin el mundo: mismo bróker, misma factura, mismas
    reglas del cuerpo (LayaLife). Se contrasta contra el mundo real (fidelity) en cada campeona."""

    def __init__(self, md: MarketData, grid: Grid, pricing: dict, initial: float, min_invested: float,
                 buy_frac: float = 0.25, frac_by: dict | None = None):
        self.grid = grid
        self.days = grid.days
        self.syms = [s for s in md.symbols]
        self.close = {}
        for s in self.syms:
            ser = md.series[s]
            self.close[s] = np.array([(b.close if (b := ser.asof(d)) else np.nan) for d in self.days])
        t = {x["name"]: x for x in pricing["server_types"]}["cx23"]
        self.hourly, self.monthly = t["hourly"], t["monthly"]
        self.ipv4_monthly = pricing["primary_ipv4_monthly"]
        self.daily_host = (self.monthly + self.ipv4_monthly) / 30.4
        self.initial, self.min_invested, self.buy_frac = initial, min_invested, buy_frac
        # tamaño de compra por símbolo (núcleo + satélite: p. ej. {"SPY": 0.75} y el resto buy_frac)
        self.frac_by = frac_by or {}

    # --- factura diaria de Hetzner, idéntica al gemelo: incremento del acumulado del mes con tope
    def _capped(self, h: float) -> float:
        return min(h * self.hourly, self.monthly) + min(h * IPV4_HOURLY, self.ipv4_monthly)

    def _hours(self, created: datetime, a: datetime, b: datetime) -> int:
        lo, hi = max(created, a), b
        return math.ceil((hi - lo).total_seconds() / 3600 - 1e-9) if hi > lo else 0

    @staticmethod
    def fee_cents(side: str, qty: float, notional: float) -> int:
        comm = min(max(qty * COMMISSION_PER_SHARE, COMMISSION_MIN), notional * COMMISSION_MAX_PCT)
        if side == "sell":
            comm += notional * SEC_FEE_RATE + min(qty * TAF_PER_SHARE, TAF_MAX)
        return int(math.ceil(comm * 100 - 1e-9))

    def run(self, start_i: int, sessions: int, lookup, fixed: str = "", keep_log: bool = False,
            track: bool = False) -> dict:
        """lookup(sym, day_iso, held) -> probs[3] o None (sin estado ese día)."""
        days = self.days[start_i: start_i + sessions + 1]
        d0 = days[0]
        f = {s: 100.0 / self.close[s][start_i] for s in self.syms
             if not np.isnan(self.close[s][start_i]) and self.close[s][start_i] > 0}
        cash = to_cents(self.initial)
        qty = {}                        # sym -> unidades enmascaradas
        avg = {}
        created = datetime.combine(d0, dtime(13, 30))
        unpaid = []                     # [céntimos, intentos]
        next_mid = datetime.combine(d0 + timedelta(days=1), dtime(0, 0))
        alive, trades, fees, mandate_buys, buys_own, own = True, 0, 0, 0, 0, 0
        pending, curve, movs, lote = [], [], [], []
        # rentabilidad del dinero INVERTIDO (ponderada en el tiempo): cada día, lo que ganan las
        # posiciones de ayer a precio de hoy, menos comisiones y horquilla pagadas hoy
        twr, dias_inv, prev_q, fees_dia = 1.0, 0, {}, [0]

        def px(s, i):
            return self.close[s][start_i + i] * f[s]

        def equity(i):
            return cash + sum(to_cents(q * px(s, i)) for s, q in qty.items() if q > 0)

        def order(s, side, q, i):
            nonlocal cash, trades, fees
            p = px(s, i)
            half = p * SPREAD_BPS / 1e4
            fill = p + half if side == "buy" else p - half
            delta = q * fill
            fc = self.fee_cents(side, q, delta)
            if side == "buy":
                if to_cents(delta) + fc > cash:
                    return False
                cash -= to_cents(delta) + fc
                tot = qty.get(s, 0.0) * avg.get(s, fill) + q * fill
                qty[s] = qty.get(s, 0.0) + q
                avg[s] = tot / qty[s]
            else:
                if qty.get(s, 0.0) < q:
                    return False
                cash += to_cents(delta) - fc
                qty[s] -= q
            trades += 1
            fees += fc
            fees_dia[0] += fc / 100 + q * half           # comisión + horquilla, en euros
            if keep_log:
                movs.append({"t": days[i].isoformat(), "op": side, "sym": s, "qty": round(q, 4),
                             "precio": round(fill, 2), "eur": round((-1 if side == "buy" else 1) * delta, 2),
                             "comision": fc / 100})
            return True

        def pos_eur(s, i):
            return qty.get(s, 0.0) * px(s, i) if qty.get(s, 0.0) > 0 else 0.0

        def buy_amount(i, s=None):
            free = cash / 100 - RESERVE_DAYS * self.daily_host
            frac = self.frac_by.get(s, self.buy_frac)
            amt = min(free, max(frac * equity(i) / 100, MIN_TICKET_EUR))
            return amt if amt >= MIN_TICKET_EUR else 0.0

        for n, d in enumerate(days):
            if track and n > 0:
                base = sum(q * px(s, n - 1) for s, q in prev_q.items() if q > 0)
                if base > 0:
                    pnl = sum(q * (px(s, n) - px(s, n - 1)) for s, q in prev_q.items() if q > 0)
                    twr *= 1 + (pnl - fees_dia[0]) / base
                    dias_inv += 1
            fees_dia[0] = 0.0
            now = datetime.combine(d, dtime(15, 0))
            # 1. medianoches pasadas: factura del día que acaba + reintentos de impagos
            while next_mid <= now and alive:
                ps = next_mid - timedelta(days=1)                # el día que se factura
                ms = ps.replace(day=1, hour=0, minute=0)         # su mes (el tope es mensual)
                ch = self._capped(self._hours(created, ms, next_mid)) - self._capped(self._hours(created, ms, ps))
                c = to_cents(ch)
                for u in list(unpaid):                         # reintentos de días anteriores
                    if cash >= u[0]:
                        cash -= u[0]; unpaid.remove(u)
                    else:
                        u[1] += 1
                        if u[1] >= GRACE_DAYS:
                            alive = False
                if c > 0:
                    if cash >= c:
                        cash -= c
                    else:
                        unpaid.append([c, 0])
                next_mid += timedelta(days=1)
            if not alive:
                break
            # 2. órdenes decididas en la sesión anterior (ventas primero, luego compras por convicción)
            for s, act, _c in pending:
                if act == SELL and qty.get(s, 0) > 0:
                    order(s, "sell", qty[s], n)
            for s, act, _c in sorted(pending, key=lambda x: -x[2]):
                if act != BUY:
                    continue
                amt = buy_amount(n, s)
                if amt <= 0:
                    continue
                p = px(s, n)
                q = math.floor(amt / (p * (1 + SPREAD_BPS / 1e4)) * 1e4) / 1e4
                fee = self.fee_cents("buy", q, q * p) / 100
                q = math.floor((amt - fee) / (p * (1 + SPREAD_BPS / 1e4)) * 1e4) / 1e4
                if q > 0:
                    order(s, "buy", q, n)
            pending = []
            # 3. tesorería: pagar va antes que cualquier tesis
            if cash / 100 < TREASURY_DAYS * self.daily_host:
                need = RESERVE_DAYS * self.daily_host
                for s in sorted([s for s in qty if qty[s] > 0], key=lambda s: -pos_eur(s, n)):
                    falta = need - cash / 100
                    if falta <= 0:
                        break
                    p = px(s, n)
                    q = qty[s] if falta + 2 >= qty[s] * p * 0.8 else math.ceil((falta + 2) / p * 1e4) / 1e4
                    order(s, "sell", min(q, qty[s]), n)
            curve.append(round(equity(n) / 100, 2))
            prev_q = {s: q for s, q in qty.items() if q > 0}
            if n == len(days) - 1 or n % DECIDE_EVERY:
                continue
            # 4. decidir
            if fixed == "cash":
                continue
            if fixed == "index":
                if n == 0:
                    old = self.buy_frac
                    self.buy_frac = 1.0
                    pending = [("SPY", BUY, 1.0)]
                    self._restore = old
                continue
            dk = d.isoformat()
            eq = equity(n) / 100
            cand = []
            for s in f:
                held = qty.get(s, 0.0) > 0
                p = lookup(s, dk, held)
                if p is None:
                    continue
                act = int(np.argmax(p))
                cand.append((s, p, act, held))
                own += 1
                if act == BUY:
                    buys_own += 1
                if act != HOLD:
                    pending.append((s, act, float(p[act])))
            # mandato (idéntico a LayaLife._mandate): conservar antes que rotar, luego comprar favoritas
            if self.min_invested > 0 and eq > 0 and cand:
                ventas = [(s, c) for s, act, c in pending if act == SELL and pos_eur(s, n) > 0]
                vendidas = {s for s, _ in ventas}
                inv = sum(pos_eur(s, n) for s in qty if s not in vendidas) / eq
                compras = {s for s, act, _ in pending if act == BUY}
                inv += len(compras) * self.buy_frac
                for s, _c in sorted(ventas, key=lambda x: x[1]):
                    if inv >= self.min_invested:
                        break
                    pending = [x for x in pending if not (x[0] == s and x[1] == SELL)]
                    vendidas.discard(s)
                    inv += pos_eur(s, n) / eq
                for s, p, act, held in sorted(cand, key=lambda x: -x[1][BUY]):
                    if inv >= self.min_invested:
                        break
                    if s in compras or s in vendidas or pos_eur(s, n) > 0:
                        continue
                    pending.append((s, BUY, float(p[BUY])))
                    compras.add(s)
                    inv += self.buy_frac
                    mandate_buys += 1
            if keep_log:
                lote = [{"alias": s, "act": ACTIONS[act], "p": [round(float(v), 3) for v in p], "held": held,
                         "mandate": False} for s, p, act, held in cand]
                lote += [{"alias": s, "act": "buy", "p": [0, 0, round(c, 3)], "held": False, "mandate": True}
                         for s, act, c in pending if act == BUY and s not in {x[0] for x in cand if x[2] == BUY}]
        if fixed == "index" and hasattr(self, "_restore"):
            self.buy_frac = self._restore
        last = len(curve) - 1
        final = equity(last) / 100 if alive and curve else 0.0
        out = {"final": final if alive else 0.0, "alive": alive, "trades": trades, "fees": fees / 100,
               "mandate_buys": mandate_buys, "own": own, "buys_own": buys_own, "curve": curve,
               "start": d0.isoformat()}
        if track:
            out["twr"] = twr - 1
            out["dias_invertido"] = dias_inv
        if keep_log:
            out["movs"] = movs
            out["lote"] = lote
            out["positions"] = [{"alias": s, "value": round(pos_eur(s, last), 2),
                                 "pl": round(px(s, last) / avg[s] - 1, 4) if avg.get(s) else 0.0, "since": ""}
                                for s in qty if qty[s] > 1e-9]
            out["cash"] = cash / 100
        return out


# ============================================================================ premio por LEER los datos
def residual_forward(md, grid: Grid, cache: dict, H: int = 20) -> np.ndarray:
    """Para cada estado (sin posición): lo que sube la acción en las H sesiones siguientes a la
    ejecución MENOS lo que explica el mercado (beta de 1 año, solo con pasado × índice).
    Solo sirve para puntuar DESPUÉS de la vida (como los euros); nunca entra en el estado."""
    from econosim.market import quant
    spy = md.series["SPY"]
    fwd = np.full(len(cache["M"]), np.nan, dtype=np.float32)
    beta_cache = {}
    for (sym, dk, held), r in cache["index"].items():
        if held != "0":
            continue
        d = date.fromisoformat(dk)
        i0 = grid.idx.get(d)
        if i0 is None or i0 + 1 + H >= len(grid.days):
            continue
        s = md.series[sym]
        b1, b2 = s.asof(grid.days[i0 + 1]), s.asof(grid.days[i0 + 1 + H])
        m1, m2 = spy.asof(grid.days[i0 + 1]), spy.asof(grid.days[i0 + 1 + H])
        if not (b1 and b2 and m1 and m2):
            continue
        key = (sym, dk)
        if key not in beta_cache:
            k, ks = s.index_of(d), spy.index_of(d)
            bc = quant.beta_corr([x.close for x in s.bars[max(0, k - 252): k + 1]],
                                 [x.close for x in spy.bars[max(0, ks - 252): ks + 1]])
            beta_cache[key] = bc["beta"] if bc else 1.0
        fwd[r] = (b2.close / b1.close - 1) - beta_cache[key] * (m2.close / m1.close - 1)
    return fwd


def rank_ic(pbuy: np.ndarray, fwd: np.ndarray, groups: list) -> float:
    """IC de rangos medio por fecha: ¿las que más quiere comprar son las que más suben sobre el mercado?"""
    ics = []
    for g in groups:
        a, b = pbuy[g], fwd[g]
        if len(g) < 8:
            continue
        ra, rb = a.argsort().argsort().astype(np.float64), b.argsort().argsort().astype(np.float64)
        ra -= ra.mean(); rb -= rb.mean()
        d = math.sqrt((ra ** 2).sum() * (rb ** 2).sum())
        if d:
            ics.append((ra * rb).sum() / d)
    return float(np.mean(ics)) if ics else 0.0


# ============================================================================ evolución
class Evolution:
    def __init__(self, a):
        self.a = a
        self.run = Path(a.run) if a.run else RUNS / ("es-" + datetime.now().strftime("%Y%m%d-%H%M"))
        self.run.mkdir(parents=True, exist_ok=True)
        self.md, self.fu = MarketData(), Fundamentals()
        self.grid = Grid(self.md)
        self.pol = LayaPolicy() if a.brain == "laya" else None     # B no necesita a Laya
        self.dev = self.pol.device if self.pol else torch.device("cpu")
        from econosim.market.calendar import UTC
        from econosim.world import World
        w = World(datetime(2020, 1, 1, tzinfo=UTC))
        self.sim = FastSim(self.md, self.grid, w.load_pricing("hetzner"), a.initial_eur, a.min_invested)
        self.progress = {"run": self.run.name, "metodo": "estrategias evolutivas (OpenAI ES) sobre el scorer de Laya",
                         "config": vars(a), "generations": [], "baselines": {}, "champion": None, "status": "arrancando"}
        self.phase, self.gen, self._live_t = "arrancando", 0, 0.0
        self.last_life = None

    # ---- panel ------------------------------------------------------------------
    def live(self, phase: str = "", force: bool = False) -> None:
        now = time.time()
        if not force and now - self._live_t < 0.7:
            return
        self._live_t = now
        if phase:
            self.phase = phase
        snap = {"t": now, "run": self.run.name, "phase": self.phase, "gen": self.gen,
                "config": {"min_invested": self.a.min_invested, "idle_penalty": 0.0, "sessions": self.a.sessions,
                           "initial_eur": self.a.initial_eur, "train_lives": self.a.lives, "val_lives": self.a.val_lives,
                           "population": self.a.pop},
                "baselines": {k: {"val": v["val"]["score"], "test": v["test"]["score"]}
                              for k, v in self.progress["baselines"].items()},
                "champion": self.progress.get("champion"),
                "generations": [{"gen": g["gen"], "val": g["val"]["score"], "test": (g.get("test") or {}).get("score"),
                                 "buy": g["val"]["actions"].get("buy", 0), "trades": g["val"]["trades"],
                                 "accepted": g["accepted"], "train": g.get("train_fitness")}
                                for g in self.progress["generations"]]}
        if self.last_life:
            snap["life"] = self.last_life
        tmp = self.run / "live.json.tmp"
        try:
            tmp.write_text(json.dumps(snap, default=str), encoding="utf-8")
            tmp.replace(self.run / "live.json")
        except OSError:
            pass

    def save(self) -> None:
        tmp = self.run / "progress.json.tmp"
        tmp.write_text(json.dumps(self.progress, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.run / "progress.json")

    # ---- evaluar una política (theta) en un conjunto de vidas ----------------------
    def rows_for(self, cache, starts: list[int]):
        """Filas de la caché que pueden hacer falta en esas vidas (todas las decisiones, ambas variantes)."""
        need = set()
        for i in starts:
            for n in range(0, self.a.sessions, DECIDE_EVERY):
                if i + n >= len(self.grid.days):
                    break
                dk = self.grid.days[i + n].isoformat()
                for s in self.sim.syms:
                    for h in ("0", "1"):
                        r = cache["index"].get((s, dk, h))
                        if r is not None:
                            need.add(r)
        if getattr(self, "_full_X", None) is not None:
            return list(range(len(cache["M"]))), self._full_X          # el híbrido rankea por fecha: caché entera
        rows = sorted(need)
        X = torch.from_numpy(cache["M"][rows].astype(np.float32)).to(self.dev)
        if "fwd" in cache:
            por_fecha = {}
            keys = cache.setdefault("_keys", {i: k for k, i in cache["index"].items()})
            for j, r in enumerate(rows):
                if not np.isnan(cache["fwd"][r]):
                    por_fecha.setdefault(keys[r][1], []).append(j)
            self._ic_groups[id(X)] = (list(por_fecha.values()), cache["fwd"][rows])
        return rows, X

    def index_finals(self, starts) -> np.ndarray:
        key = tuple(starts)
        if key not in self._idx_cache:
            self._idx_cache[key] = np.array([self.sim.run(i, self.a.sessions, lambda *_: None, fixed="index")["final"]
                                             for i in starts])
            if len(self._idx_cache) > 64:
                self._idx_cache.pop(next(iter(self._idx_cache)))
        return self._idx_cache[key]

    def score_lives(self, theta, cache, starts, rows, X, keep_log=False, fixed="", subsets=None):
        if fixed:
            res = [self.sim.run(i, self.a.sessions, lambda *_: None, fixed=fixed, keep_log=keep_log) for i in starts]
        else:
            if isinstance(self.scorer, HybridBrain):
                c = self.scorer.core(theta)
                self.sim.frac_by = {"SPY": c}
                self.sim.buy_frac = (1 - c) / HybridBrain.N
            P = self.scorer.probs(theta, X)
            pos = {r: j for j, r in enumerate(rows)}
            idx = cache["index"]

            def make_lookup(sub):
                def lookup(s, dk, held):
                    # el sorteo es de EMPRESAS: los ETF (índices e inversos) están siempre disponibles
                    if sub is not None and s not in sub and s not in ETF_ALWAYS:
                        return None
                    r = idx.get((s, dk, "1" if held else "0"))
                    return None if r is None or r not in pos else P[pos[r]]
                return lookup
            res = [self.sim.run(i, self.a.sessions, make_lookup(subsets[j] if subsets else None), keep_log=keep_log)
                   for j, i in enumerate(starts)]
        ic = 0.0
        if not fixed and id(X) in self._ic_groups:
            groups, fw = self._ic_groups[id(X)]
            ic = rank_ic(P[:, BUY], fw, groups)
        finals = np.array([r["final"] for r in res])
        exceso = finals - self.index_finals(starts) if not fixed else np.zeros(len(finals))
        own = sum(r["own"] for r in res)
        stats = {"score": float(finals.mean()), "std": float(finals.std()),
                 "ic": round(ic, 4),
                 # aptitud multiobjetivo: euros ajustados por riesgo + premio por acertar QUÉ sube
                 # más de lo que explica el mercado (leer los datos, no solo cargar beta)
                 "fitness": (float(0.5 * np.median(exceso) + 0.5 * np.percentile(exceso, 25)) + self.a.ic_weight * ic
                             if self.a.fitness == "consistencia"
                             else float(finals.mean() - self.a.risk * finals.std() + self.a.ic_weight * ic)),
                 # vida a vida contra el índice en las mismas fechas
                 "gana_al_indice": round(float((exceso > 0).mean()), 3) if not fixed else None,
                 "exceso_mediana": round(float(np.median(exceso)), 2),
                 "exceso_p25": round(float(np.percentile(exceso, 25)), 2),
                 "survival": float(np.mean([r["alive"] for r in res])),
                 "trades": float(np.mean([r["trades"] for r in res])),
                 "fees": float(np.mean([r["fees"] for r in res])),
                 "mandate_buys": float(np.mean([r["mandate_buys"] for r in res])),
                 "actions": {"buy": round(sum(r["buys_own"] for r in res) / max(1, own), 4)}}
        return stats, res

    # ---- comprobación en el mundo REAL de ECONOSIM ---------------------------------
    def real_check(self, theta, cache, starts: list[int]) -> list[dict]:
        rows, X = self.rows_for(cache, starts)
        P = self.scorer.probs(theta, X)
        pos = {r: j for j, r in enumerate(rows)}

        def key_policy(sym, d, held):
            r = cache["index"].get((sym, d.isoformat(), "1" if held else "0"))
            return P[pos[r]] if r is not None and r in pos else np.array([0.0, 1.0, 0.0])
        out = []
        for i in starts:
            L = LayaLife(self.md, self.fu, f"check-{i}", self.grid.days[i], sessions=self.a.sessions,
                         initial_eur=self.a.initial_eur, min_invested=self.a.min_invested)
            r = L.run(None, key_policy=key_policy)
            fast = self.sim.run(i, self.a.sessions, lambda s, dk, h: (P[pos[cache["index"][(s, dk, "1" if h else "0")]]]
                                                                     if (s, dk, "1" if h else "0") in cache["index"]
                                                                     and cache["index"][(s, dk, "1" if h else "0")] in pos
                                                                     else None))
            out.append({"start": str(self.grid.days[i]), "mundo_real": round(r.score, 2), "rapido": round(fast["final"], 2),
                        "ops_real": r.trades, "ops_rapido": fast["trades"]})
        return out

    # ---- bucle ---------------------------------------------------------------------
    def loop(self) -> None:
        a = self.a
        log(self.run, f"ES · cerebro {a.brain} · {self.run.name} · device {self.dev} · población {a.pop} · sigma {a.sigma} · lr {a.lr}")
        caches = {}
        for split in ("train", "val", "test"):
            t = time.time()
            if a.brain == "laya":
                caches[split] = build_cache(self.pol, self.md, self.fu, self.grid, split, a.sessions,
                                            on_progress=lambda m: self.live(m, force=False))
            else:      # "num" y "factor" leen los mismos números
                caches[split] = build_num_cache(self.md, self.fu, self.grid, split, a.sessions,
                                                on_progress=lambda m: self.live(m, force=False))
            log(self.run, f"caché {split}: {len(caches[split]['index'])} estados ({time.time() - t:.0f}s)")
        # el IC sin mercado se calcula siempre (para informar); solo PREMIA si ic_weight > 0
        self._ic_groups = {}
        self._idx_cache = {}
        for sp in ("train", "val", "test"):
            caches[sp]["fwd"] = residual_forward(self.md, self.grid, caches[sp])
        if a.ic_weight:
            log(self.run, f"premio por leer los datos: IC sin mercado × {a.ic_weight}")
        if a.brain == "laya":
            self.scorer = Scorer(self.pol)
        elif a.brain == "factor":
            for sp in ("train", "val", "test"):
                build_factor_matrix(caches[sp])
            self.scorer = FactorBrain()
            log(self.run, "punto de partida: " + self.scorer.describe(self.scorer.theta))
        else:
            normalize_num(caches)
            self.scorer = MLPScorer(caches["train"]["M"].shape[1], hidden=a.hidden, seed=a.seed)
        log(self.run, f"scorer: {self.scorer.n_params() / 1e6:.2f} M parámetros evolucionan")
        rng = random.Random(a.seed)
        val_starts = sorted(rng.sample(self.grid.starts("val"), a.val_lives))
        test_starts = sorted(rng.sample(self.grid.starts("test"), a.val_lives))
        vrows, vX = self.rows_for(caches["val"], val_starts)
        trows, tX = self.rows_for(caches["test"], test_starts)
        for name in ("cash", "index"):
            self.progress["baselines"][name] = {
                "val": self.score_lives(None, caches["val"], val_starts, None, None, fixed=name)[0],
                "test": self.score_lives(None, caches["test"], test_starts, None, None, fixed=name)[0]}
            b = self.progress["baselines"][name]
            log(self.run, f"referencia {name}: val {b['val']['score']:.2f} € · test {b['test']['score']:.2f} €")
        theta = [t.clone() for t in self.scorer.theta]
        v0, vres = self.score_lives(theta, caches["val"], val_starts, vrows, vX, keep_log=True)
        t0, _ = self.score_lives(theta, caches["test"], test_starts, trows, tX)
        champ = {"gen": 0, "val": v0, "test": t0}
        best_theta = [t.clone() for t in theta]
        self.progress["champion"] = champ
        self.progress["generations"].append({"gen": 0, "val": v0, "test": t0, "accepted": True})
        self.set_life(vres[0], "gen 0 · Laya sin evolucionar · validación")
        chk = self.real_check(theta, caches["val"], val_starts[:2])
        log(self.run, f"gen 0 (Laya sin evolucionar): val {v0['score']:.2f} € · test {t0['score']:.2f} € · compra propia "
                      f"{v0['actions']['buy']:.1%} · mundo real vs rápido {chk}")
        self.progress["fidelity"] = [chk]
        self.progress["status"] = "evolucionando"
        self.save()
        self.live("evolucionando", force=True)
        opt = Adam([t.shape for t in theta], a.lr)
        half = a.pop // 2
        g = 0
        while g < a.max_gens:
            if (self.run / "STOP").exists():
                log(self.run, "STOP: paro")
                break
            g += 1
            self.gen = g
            t_gen = time.time()
            starts = rng.sample(self.grid.starts("train"), a.lives)          # misma muestra para toda la población
            rows, X = self.rows_for(caches["train"], starts)
            self.scorer.theta = theta
            fits, seeds = [], []
            for k in range(half):
                seed = g * 100003 + k
                eps = self.scorer.noise(seed)
                fp = self.score_lives(self.scorer.perturbed(eps, +1, a.sigma), caches["train"], starts, rows, X)[0]["fitness"]
                fm = self.score_lives(self.scorer.perturbed(eps, -1, a.sigma), caches["train"], starts, rows, X)[0]["fitness"]
                fits += [fp, fm]
                seeds.append(seed)
                self.live(f"gen {g} · individuo {2 * k + 2}/{2 * half}")
            # aptitud -> rangos centrados en [-0.5, 0.5] (OpenAI ES)
            f = np.array(fits)
            ranks = np.empty_like(f)
            ranks[f.argsort()] = np.arange(len(f))
            shaped = ranks / (len(f) - 1) - 0.5
            grad = [torch.zeros_like(t) for t in theta]
            for k, seed in enumerate(seeds):
                w = shaped[2 * k] - shaped[2 * k + 1]
                if w == 0:
                    continue
                for j, e in enumerate(self.scorer.noise(seed)):
                    grad[j] += w * e
            grad = [gr / (len(f) * a.sigma) for gr in grad]
            steps = opt.step(grad)
            # paso de Adam relativo al tamaño típico de cada tensor (lr=0.01 -> 1 % de su escala)
            theta = [t + (st * r).to(t.device) for t, st, r in zip(theta, steps, self.scorer.rms)]
            # la media de la población, en validación: ¿generaliza?
            cand, cres = self.score_lives(theta, caches["val"], val_starts, vrows, vX, keep_log=True)
            better = cand["fitness"] > champ["val"]["fitness"] and cand["actions"]["buy"] >= a.min_buy_share
            rec = {"gen": g, "train_fitness": float(f.mean()), "train_best": float(f.max()), "val": cand,
                   "accepted": better, "secs": round(time.time() - t_gen, 1)}
            if better:
                tst, _ = self.score_lives(theta, caches["test"], test_starts, trows, tX)
                rec["test"] = tst
                champ = {"gen": g, "val": cand, "test": tst}
                best_theta = [t.clone() for t in theta]
                self.progress["champion"] = champ
                if self.pol is not None:
                    self.scorer.apply_to(self.pol, best_theta)
                    self.pol.save_head(self.run / "champion.safetensors")
                else:
                    torch.save([t.cpu() for t in best_theta], self.run / "champion_mlp.pt")
                if a.brain == "factor":
                    log(self.run, f"  campeona gen {g}: " + self.scorer.describe(best_theta))
                self.set_life(cres[0], f"campeona gen {g} · validación")
                with open(self.run / "lives.jsonl", "a", encoding="utf-8") as fh:
                    for r in cres:
                        fh.write(json.dumps({"gen": g, "split": "val", **{k: v for k, v in r.items() if k != "lote"}},
                                            default=str) + "\n")
                if g % a.check_every == 0 or len([x for x in self.progress["generations"] if x["accepted"]]) <= 3:
                    chk = self.real_check(best_theta, caches["val"], val_starts[:2])
                    self.progress.setdefault("fidelity", []).append(chk)
                    rec["fidelity"] = chk
            gan = lambda st: (st["score"] - self.progress["baselines"]["cash"]["val"]["score"]) / a.initial_eur * 100
            log(self.run, f"gen {g}: población {f.mean():.2f} (mejor {f.max():.2f}) · val {cand['score']:.2f} € "
                          f"[invertir {gan(cand):+.1f}% vs índice {gan(self.progress['baselines']['index']['val']):+.1f}%] "
                          f"(±{cand['std']:.2f}, gana al índice {cand['gana_al_indice']:.0%} de vidas, mediana {cand['exceso_mediana']:+.2f} €, "
                          f"IC sin mercado {cand['ic']:+.3f}, compra propia {cand['actions']['buy']:.1%}, {cand['trades']:.1f} ops) · "
                          f"{'NUEVA CAMPEONA · test ' + format(rec['test']['score'], '.2f') + ' €' if better else 'campeona gen ' + str(champ['gen'])}"
                          f"{' · real ' + str(rec['fidelity']) if 'fidelity' in rec else ''} · {rec['secs']}s")
            self.progress["generations"].append(rec)
            with open(self.run / "gens.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            self.progress["updated"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            self.live(f"gen {g} hecha", force=True)
        self.progress["status"] = "terminado"
        self.save()
        self.live("terminado", force=True)
        log(self.run, f"FIN · campeona gen {champ['gen']} · val {champ['val']['score']:.2f} € · test {champ['test']['score']:.2f} €")

    # ---- WALK-FORWARD: cada generación aprende en un periodo y se la juzga en el SIGUIENTE, -----
    # que no ha visto nunca. Cada vida, además, con un subconjunto al azar de empresas.
    def loop_walkforward(self) -> None:
        a = self.a
        ETF = {"SPY", "QQQ", "DIA", "IWM"} | INVERSE
        log(self.run, f"WALK-FORWARD · cerebro {a.brain} · bloques de {a.sessions} sesiones · {a.lives} vidas por bloque · "
                      f"{a.subset} empresas al azar por vida · población {a.pop}")
        cache = build_num_cache(self.md, self.fu, self.grid, "all", a.sessions,
                                on_progress=lambda m: self.live(m, force=False))
        log(self.run, f"caché de toda la historia: {len(cache['index'])} estados")
        caches = {"all": cache}
        self._ic_groups, self._idx_cache = {}, {}
        cache["fwd"] = np.full(len(cache["M"]), np.nan, dtype=np.float32)
        if a.brain in ("factor", "hibrido"):
            build_factor_matrix(cache)
            if a.brain == "hibrido":
                self.scorer = HybridBrain()
                self.scorer.set_cache(cache)
                self._full_X = torch.from_numpy(cache["M"])
            else:
                self.scorer = FactorBrain()
            log(self.run, "punto de partida: " + self.scorer.describe(self.scorer.theta))
        else:
            caches.update({"train": cache})
            normalize_num({"train": cache})
            self.scorer = MLPScorer(cache["M"].shape[1], hidden=a.hidden, seed=a.seed)
        empresas = sorted({k[0] for k in cache["index"]} - ETF)
        # bloques consecutivos alineados a la rejilla de decisión
        i0 = next(i for i, d in enumerate(self.grid.days) if d >= SPLITS["all"][0] and i % DECIDE_EVERY == 0)
        bloques = list(range(i0, len(self.grid.days) - a.sessions - 1, a.sessions))
        corte = next(k for k, i in enumerate(bloques) if self.grid.days[i] >= date(2022, 1, 1))
        aprender, examen = bloques[:corte], bloques[corte:]
        log(self.run, f"{len(aprender)} bloques para avanzar ({self.grid.days[aprender[0]]} → {self.grid.days[aprender[-1]]}) · "
                      f"{len(examen)} bloques de EXAMEN FINAL guardados ({self.grid.days[examen[0]]} → {self.grid.days[examen[-1]]})")
        rng = random.Random(a.seed)

        def vidas(b: int, n: int):
            return [b] * n, [set(rng.sample(empresas, min(a.subset, len(empresas)))) for _ in range(n)]

        def juzgar(theta, b: int, subs):
            st = [b] * len(subs)
            rows, X = self.rows_for(cache, [b])
            return self.score_lives(theta, cache, st, rows, X, subsets=subs)

        theta = [t.clone() for t in self.scorer.theta]
        champ_theta = [t.clone() for t in theta]
        opt = Adam([t.shape for t in theta], a.lr)
        half = a.pop // 2
        historial = []                 # la campeona VIGENTE juzgada en un bloque que no ha visto
        # ORDEN CAÓTICO con purga y embargo (López de Prado): se aprende en un bloque al azar y se
        # juzga en otro al azar que NUNCA se ha tocado y que no está pegado al de aprendizaje.
        g, vuelta = 0, 1
        tocados: set = set()
        n_bl = len(aprender)
        while g < a.max_gens:
            if (self.run / "STOP").exists():
                log(self.run, "STOP: paro")
                break
            g += 1
            self.gen = g
            t0 = time.time()
            libres = [j for j in range(n_bl) if j not in tocados]
            if len(libres) < 1:
                if vuelta == 1:
                    self._examen_final(champ_theta, examen, vidas, juzgar)
                vuelta += 1
                tocados = set()
                libres = list(range(n_bl))
                log(self.run, f"── VUELTA {vuelta}: a partir de aquí los periodos YA SE HAN VISTO ──")
            j_judge = rng.choice(libres)
            cand_learn = [j for j in range(n_bl) if abs(j - j_judge) > 1]          # embargo: nunca pegado
            j_learn = rng.choice(cand_learn)
            tocados.update({j_judge, j_learn})
            b_learn, b_next = aprender[j_learn], aprender[j_judge]
            # 1. aprender en el bloque k (todas las variantes en las MISMAS vidas)
            st, subs = vidas(b_learn, a.lives)
            rows, X = self.rows_for(cache, [b_learn])
            self.scorer.theta = theta
            fits, seeds = [], []
            for j in range(half):
                seed = g * 100003 + j
                eps = self.scorer.noise(seed)
                fits.append(self.score_lives(self.scorer.perturbed(eps, +1, a.sigma), cache, st, rows, X, subsets=subs)[0]["fitness"])
                fits.append(self.score_lives(self.scorer.perturbed(eps, -1, a.sigma), cache, st, rows, X, subsets=subs)[0]["fitness"])
                seeds.append(seed)
            f = np.array(fits)
            ranks = np.empty_like(f)
            ranks[f.argsort()] = np.arange(len(f))
            shaped = ranks / (len(f) - 1) - 0.5
            grad = [torch.zeros_like(t) for t in theta]
            for j, seed in enumerate(seeds):
                w = shaped[2 * j] - shaped[2 * j + 1]
                for q, e in enumerate(self.scorer.noise(seed)):
                    grad[q] += w * e
            grad = [gr / (len(f) * a.sigma) for gr in grad]
            theta = [t + (s_ * r).to(t.device) for t, s_, r in zip(theta, opt.step(grad), self.scorer.rms)]
            # 2. juzgar en el bloque SIGUIENTE, nunca visto: campeona vigente vs candidata, mismas vidas
            _, subs2 = vidas(b_next, a.lives)
            ch, _ = juzgar(champ_theta, b_next, subs2)
            ca, _ = juzgar(theta, b_next, subs2)
            historial.append({"gen": g, "vuelta": vuelta, "bloque": str(self.grid.days[b_next]),
                              "gana_al_indice": ch["gana_al_indice"], "exceso_mediana": ch["exceso_mediana"],
                              "euros": ch["score"]})
            acepta = ca["fitness"] > ch["fitness"] and ca["actions"]["buy"] >= a.min_buy_share
            if acepta:
                champ_theta = [t.clone() for t in theta]
                torch.save([t.cpu() for t in champ_theta], self.run / "champion_mlp.pt")
            ult = [h for h in historial if h["vuelta"] == 1]
            bloques_ganados = sum(1 for h in ult if h["exceso_mediana"] > 0)
            rec = {"gen": g, "vuelta": vuelta, "bloque_aprende": str(self.grid.days[b_learn]),
                   "bloque_juzga": str(self.grid.days[b_next]), "val": ca, "campeona_en_bloque": ch,
                   "accepted": acepta, "secs": round(time.time() - t0, 1)}
            self.progress["generations"].append(rec)
            self.progress["walkforward"] = {"historial": historial,
                                            "bloques_ganados_vuelta1": f"{bloques_ganados}/{len(ult)}"}
            log(self.run, f"gen {g} · aprende {self.grid.days[b_learn]} · juzgada en {self.grid.days[b_next]} "
                          f"({'nunca visto' if vuelta == 1 else 'ya visto'}): "
                          f"campeona gana al índice en {ch['gana_al_indice']:.0%} de vidas (mediana {ch['exceso_mediana']:+.2f} €) · "
                          f"candidata {ca['gana_al_indice']:.0%} ({ca['exceso_mediana']:+.2f} €) · "
                          f"{'NUEVA CAMPEONA' if acepta else 'sigue la campeona'} · bloques nuevos ganados {bloques_ganados}/{len(ult)}"
                          f"{' [ya visto]' if vuelta > 1 else ''} · {rec['secs']}s")
            if acepta and a.brain in ("factor", "hibrido"):
                log(self.run, "  campeona: " + self.scorer.describe(champ_theta))
            self.save()
            self.live(f"gen {g} · vuelta {vuelta}", force=True)
            self.last_champ_theta = champ_theta
        self.progress["status"] = "terminado"
        self.save()
        log(self.run, "FIN walk-forward")

    def _examen_final(self, champ_theta, examen, vidas, juzgar) -> None:
        """Una sola vez, al acabar la primera vuelta: 2022-2025, siempre en el FUTURO de todo lo aprendido."""
        fin = []
        for b in examen:
            _, sx = vidas(b, self.a.lives)
            r, _ = juzgar(champ_theta, b, sx)
            fin.append({"bloque": str(self.grid.days[b]), "gana_al_indice": r["gana_al_indice"],
                        "exceso_mediana": r["exceso_mediana"], "euros": r["score"]})
        gan = sum(1 for x in fin if x["exceso_mediana"] > 0)
        self.progress["examen_final"] = {"bloques": fin, "ganados": f"{gan}/{len(fin)}"}
        log(self.run, f"EXAMEN FINAL 2022-2025 (nunca usado): la campeona gana al índice en {gan}/{len(fin)} "
                      f"bloques · " + " · ".join(f"{x['bloque'][:7]} {x['exceso_mediana']:+.2f}€" for x in fin))
        self.save()

    def set_life(self, r: dict, mode: str) -> None:
        self.last_life = {"seed": "val", "start": r["start"], "day": len(r["curve"]) - 1, "total": self.a.sessions,
                          "date": "", "equity": r["curve"][-1] if r["curve"] else None, "cash": r.get("cash"),
                          "initial": self.a.initial_eur, "trades": r["trades"], "fees": r["fees"],
                          "mandate_buys": r["mandate_buys"], "alive": r["alive"], "positions": r.get("positions", []),
                          "curve": r["curve"], "mode": mode, "decisions": r.get("lote", [])}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--pop", type=int, default=30, help="individuos por generación (pares en espejo)")
    ap.add_argument("--sigma", type=float, default=0.05, help="ruido relativo al tamaño típico de cada tensor")
    ap.add_argument("--lr", type=float, default=0.01, help="Adam, relativo al tamaño típico de cada tensor")
    ap.add_argument("--lives", type=int, default=16, help="vidas de entrenamiento por generación (las mismas para todos)")
    ap.add_argument("--val-lives", type=int, default=24)
    ap.add_argument("--sessions", type=int, default=126)
    ap.add_argument("--initial-eur", type=float, default=50.0)
    ap.add_argument("--min-invested", type=float, default=0.5)
    ap.add_argument("--min-buy-share", type=float, default=0.02,
                    help="una campeona tiene que comprar por sí misma en al menos esta fracción de decisiones")
    ap.add_argument("--risk", type=float, default=0.5, help="aptitud = media − risk × desviación entre vidas")
    ap.add_argument("--ic-weight", type=float, default=0.0,
                    help="premio en la aptitud por el IC sin mercado (0 = solo euros; 30 ~ 1,5 € por IC 0,05)")
    ap.add_argument("--train-from", default="", help="inicio del tramo de entrenamiento (AAAA-MM-DD)")
    ap.add_argument("--fitness", choices=["media", "consistencia"], default="media",
                    help="media = euros medios − risk·dispersión; consistencia = mediana y peor cuarto de la "
                         "ventaja sobre el índice VIDA A VIDA (gana casi siempre, no a veces mucho)")
    ap.add_argument("--walk-forward", action="store_true",
                    help="cada generación aprende en un bloque de 6 meses y se la juzga en el SIGUIENTE, nunca visto")
    ap.add_argument("--subset", type=int, default=40, help="empresas al azar por vida en walk-forward")
    ap.add_argument("--max-gens", type=int, default=10 ** 9)
    ap.add_argument("--check-every", type=int, default=10, help="cada cuántas generaciones se comprueba en el mundo real")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--brain", choices=["laya", "num", "factor", "hibrido"], default="laya",
                    help="laya = la cabeza de Laya sobre el texto; num = red pequeña sobre los mismos números")
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--run", default="")
    a = ap.parse_args()
    if a.walk_forward:
        a.sessions = 125                     # bloque = 25 decisiones semanales ≈ 6 meses, alineado a la rejilla
        a.fitness = "consistencia"
        Evolution(a).loop_walkforward()
        return 0
    if a.train_from:
        # entrenar con crisis dentro (p. ej. desde 2005: 2008 incluido). Antes de 2009 las
        # empresas aún no tienen cuentas en el registro: salen como "sin datos", no se inventan.
        SPLITS["train"] = (date.fromisoformat(a.train_from), SPLITS["train"][1])
    Evolution(a).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
