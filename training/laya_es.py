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
}
DECIDE_EVERY = 5


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
        lo, hi = SPLITS[split]
        return [i for i, d in enumerate(self.days) if lo <= d <= hi]

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
    path = CACHE / f"{split}_{sessions}.npz"
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
                 buy_frac: float = 0.25):
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

    def run(self, start_i: int, sessions: int, lookup, fixed: str = "", keep_log: bool = False) -> dict:
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
            if keep_log:
                movs.append({"t": days[i].isoformat(), "op": side, "sym": s, "qty": round(q, 4),
                             "precio": round(fill, 2), "eur": round((-1 if side == "buy" else 1) * delta, 2),
                             "comision": fc / 100})
            return True

        def pos_eur(s, i):
            return qty.get(s, 0.0) * px(s, i) if qty.get(s, 0.0) > 0 else 0.0

        def buy_amount(i):
            free = cash / 100 - RESERVE_DAYS * self.daily_host
            amt = min(free, max(self.buy_frac * equity(i) / 100, MIN_TICKET_EUR))
            return amt if amt >= MIN_TICKET_EUR else 0.0

        for n, d in enumerate(days):
            now = datetime.combine(d, dtime(15, 0))
            # 1. medianoches pasadas: factura del día que acaba + reintentos de impagos
            while next_mid <= now and alive:
                ms = next_mid.replace(day=1) if next_mid.day != 1 else (next_mid - timedelta(days=1)).replace(day=1)
                ps = next_mid - timedelta(days=1)
                ms = ps.replace(day=1, hour=0, minute=0)
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
                amt = buy_amount(n)
                if amt <= 0:
                    break
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
        if keep_log:
            out["movs"] = movs
            out["lote"] = lote
            out["positions"] = [{"alias": s, "value": round(pos_eur(s, last), 2),
                                 "pl": round(px(s, last) / avg[s] - 1, 4) if avg.get(s) else 0.0, "since": ""}
                                for s in qty if qty[s] > 1e-9]
            out["cash"] = cash / 100
        return out


# ============================================================================ evolución
class Evolution:
    def __init__(self, a):
        self.a = a
        self.run = Path(a.run) if a.run else RUNS / ("es-" + datetime.now().strftime("%Y%m%d-%H%M"))
        self.run.mkdir(parents=True, exist_ok=True)
        self.md, self.fu = MarketData(), Fundamentals()
        self.grid = Grid(self.md)
        self.pol = LayaPolicy()
        from econosim.world import World
        w = World(datetime(2020, 1, 1, tzinfo=__import__("datetime").timezone.utc))
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
        rows = sorted(need)
        X = torch.from_numpy(cache["M"][rows].astype(np.float32)).to(self.pol.device)
        return rows, X

    def score_lives(self, theta, cache, starts, rows, X, keep_log=False, fixed=""):
        if fixed:
            res = [self.sim.run(i, self.a.sessions, lambda *_: None, fixed=fixed, keep_log=keep_log) for i in starts]
        else:
            P = self.scorer.probs(theta, X)
            pos = {r: j for j, r in enumerate(rows)}
            idx = cache["index"]

            def lookup(s, dk, held):
                r = idx.get((s, dk, "1" if held else "0"))
                return None if r is None or r not in pos else P[pos[r]]
            res = [self.sim.run(i, self.a.sessions, lookup, keep_log=keep_log) for i in starts]
        finals = np.array([r["final"] for r in res])
        own = sum(r["own"] for r in res)
        stats = {"score": float(finals.mean()), "std": float(finals.std()),
                 "fitness": float(finals.mean() - self.a.risk * finals.std()),
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
        log(self.run, f"ES · {self.run.name} · device {self.pol.device} · población {a.pop} · sigma {a.sigma} · lr {a.lr}")
        caches = {}
        for split in ("train", "val", "test"):
            t = time.time()
            caches[split] = build_cache(self.pol, self.md, self.fu, self.grid, split, a.sessions,
                                        on_progress=lambda m: self.live(m, force=False))
            log(self.run, f"caché {split}: {len(caches[split]['index'])} estados ({time.time() - t:.0f}s)")
        self.scorer = Scorer(self.pol)
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
            theta = [t + s.to(t.device) for t, s in zip(theta, steps)]
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
                self.scorer.apply_to(self.pol, best_theta)
                self.pol.save_head(self.run / "champion.safetensors")
                self.set_life(cres[0], f"campeona gen {g} · validación")
                with open(self.run / "lives.jsonl", "a", encoding="utf-8") as fh:
                    for r in cres:
                        fh.write(json.dumps({"gen": g, "split": "val", **{k: v for k, v in r.items() if k != "lote"}},
                                            default=str) + "\n")
                if g % a.check_every == 0 or len([x for x in self.progress["generations"] if x["accepted"]]) <= 3:
                    chk = self.real_check(best_theta, caches["val"], val_starts[:2])
                    self.progress.setdefault("fidelity", []).append(chk)
                    rec["fidelity"] = chk
            log(self.run, f"gen {g}: población {f.mean():.2f} (mejor {f.max():.2f}) · val {cand['score']:.2f} € "
                          f"(±{cand['std']:.2f}, compra propia {cand['actions']['buy']:.1%}, {cand['trades']:.1f} ops) · "
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
    ap.add_argument("--max-gens", type=int, default=10 ** 9)
    ap.add_argument("--check-every", type=int, default=10, help="cada cuántas generaciones se comprueba en el mundo real")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--run", default="")
    a = ap.parse_args()
    Evolution(a).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
