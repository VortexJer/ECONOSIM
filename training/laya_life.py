"""Una vida del cerebro Laya dentro del mundo REAL de ECONOSIM, en proceso y acelerada.

Mismo mundo que vive la IA con LLM: libro contable, Hetzner cobrando cada día (y
apagando por impago), bróker con horquilla, comisión y tasas, máscara de símbolos y
precios, cuentas point-in-time. Lo único distinto es que no hay contenedor ni HTTP: el
"cuerpo" llama a la misma lógica de los gemelos directamente (AlpacaTwin.place_order),
y el reloj salta de sesión en sesión en vez de esperar. Por eso una vida de 6 meses
tarda segundos en vez de días.

Sin mirar el futuro: la decisión del día d se toma con barras <= d y se EJECUTA al
cierre de la sesión siguiente (d+1). El estado que ve Laya no contiene el símbolo real,
ni fechas, ni niveles de precio absolutos.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from econosim.ledger import to_cents                                  # noqa: E402
from econosim.market import quant                                     # noqa: E402
from econosim.market.calendar import MARKET_OPEN, UTC                 # noqa: E402
from econosim.market.data import MarketData                           # noqa: E402
from econosim.market.fundamentals import Fundamentals                 # noqa: E402
from econosim.market.mask import EpisodeMask                          # noqa: E402
from econosim.twins.alpaca import SPREAD_BPS, AlpacaTwin              # noqa: E402
from econosim.twins.fundamentals_api import FundamentalsTwin          # noqa: E402
from econosim.twins.hetzner import HetznerTwin                        # noqa: E402
from econosim.world import World                                      # noqa: E402

from laya_brain import ACTIONS                                        # noqa: E402

BENCH = "SPY"                  # referencia interna para la beta (la IA no la ve como tal)
HISTORY = 300                  # barras que miran los indicadores
LABEL_H = 20                   # horizonte de la etiqueta (sesiones)
RISK_AVERSION = 1.0            # penalización media-varianza de la etiqueta
MIN_TICKET_EUR = 5.0           # por debajo, la comisión mínima se come la operación
RESERVE_DAYS = 30              # las compras nunca dejan la caja por debajo de 30 días de hosting


def fmt_pct(x: Optional[float], nd: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def fmt(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


@dataclass
class Decision:
    alias: str
    real: str
    day: date
    state: str
    probs: np.ndarray
    action: int
    pos_eur: float
    equity_eur: float
    buy_eur: float
    utils: Optional[np.ndarray] = None


@dataclass
class LifeResult:
    seed: str
    start: date
    days: int
    alive: bool
    final_equity: float
    initial: float
    trades: int
    fees: float
    decisions: list = field(default_factory=list)
    curve: list = field(default_factory=list)

    @property
    def score(self) -> float:
        """Lo que cuenta: el patrimonio con el que acaba. Muerta = 0."""
        return self.final_equity if self.alive else 0.0


class LayaLife:
    def __init__(self, md: MarketData, fund: Fundamentals, seed: str, start_day: date,
                 sessions: int = 126, initial_eur: float = 50.0, decide_every: int = 5,
                 buy_frac: float = 0.25):
        self.md, self.seed = md, seed
        self.mask = EpisodeMask(md, seed, start_day)
        self.w = World(datetime.combine(self.mask.start_day, MARKET_OPEN, UTC), initial_eur=initial_eur)
        self.h = HetznerTwin(self.w)
        self.a = AlpacaTwin(self.w, md, self.mask)
        self.f = FundamentalsTwin(self.w, md, self.mask, fund)
        days = self.a.cal.sessions_between(self.mask.start_day, self.mask.start_day + timedelta(days=int(sessions * 1.6) + 10))
        self.sessions = days[: sessions + 1]
        self.initial = initial_eur
        self.decide_every = decide_every
        self.buy_frac = buy_frac
        self.daily_host = (self.h.types["cx23"]["monthly"] + self.h.pricing["primary_ipv4_monthly"]) / 30.4
        self.trades = 0
        self.fees_cents = 0
        self.held_since: dict[str, date] = {}

    # ---- series reales hasta d (solo motor) ---------------------------------------
    def _bars(self, real: str, d: date, n: int = HISTORY):
        s = self.md.series[real]
        i = s.index_of(d)
        return s.bars[max(0, i - n + 1): i + 1] if i >= 0 else []

    def _pos_eur(self, alias: str) -> float:
        p = self.a.positions.get(alias)
        px = self.a._masked_price(alias)
        return p.qty * px if p and p.qty > 0 and px else 0.0

    def equity(self) -> float:
        return self.a.equity_cents() / 100

    # ---- estado en texto (lo que "ve" Laya) ----------------------------------------
    def state_text(self, alias: str, d: date, day_n: int, market: dict) -> Optional[str]:
        real = self.mask.to_real(alias)
        bars = self._bars(real, d)
        if len(bars) < 60:
            return None
        f = self.mask.factor(real)
        H = [b.high * f for b in bars]; L = [b.low * f for b in bars]
        C = [b.close * f for b in bars]; V = [float(b.volume) for b in bars]
        bench = [b.close for b in self._bars(BENCH, d)] if BENCH in self.md.series else None
        ind = quant.indicators(H, L, C, V, bench)
        px = C[-1]
        m = ind["macd"] or {}
        bb = ind["bollinger"] or {}
        iv = quant.implied_vol([b.close for b in bars], bench or C, d)
        g = quant.bs("call", px, px, 30 / 365, quant.macro().risk_free(d), iv) if iv else None
        lines = [f"Stock {alias}. Life day {day_n}. Price {px:.2f}."]
        lines.append(f"Trend: 5d {fmt_pct(ind['mom_5'])}, 20d {fmt_pct(ind['mom_20'])}, 60d {fmt_pct(ind['mom_60'])}, "
                     f"120d {fmt_pct(ind['mom_120'])}, 1y {fmt_pct(ind['mom_250'])}; price vs SMA50 "
                     f"{fmt_pct(px / ind['sma_50'] - 1 if ind['sma_50'] else None)}, vs SMA200 "
                     f"{fmt_pct(px / ind['sma_200'] - 1 if ind['sma_200'] else None)}.")
        rsi = ind["rsi_14"]
        lines.append(f"Oscillators: RSI14 {fmt(rsi, 0)}{' overbought' if rsi and rsi > 70 else ' oversold' if rsi and rsi < 30 else ''}, "
                     f"MACD histogram {fmt(m.get('hist'), 3)} ({'bullish' if (m.get('hist') or 0) > 0 else 'bearish'}), "
                     f"Bollinger %B {fmt(bb.get('pct_b'))}, Williams %R {fmt(ind['williams_r_14'], 0)}, "
                     f"ADX {fmt(ind['adx_14'], 0)}{' strong trend' if (ind['adx_14'] or 0) > 25 else ''}.")
        lines.append(f"Risk: volatility 20d {fmt_pct(ind['vol_20'], 0)}, 60d {fmt_pct(ind['vol_60'], 0)} a year, "
                     f"ATR {fmt_pct(ind['atr_pct'])} of price, {fmt_pct(ind['from_high_52w'])} from 1y high, "
                     f"max drawdown 1y {fmt_pct(ind['max_drawdown_1y'], 0)}, beta {fmt(ind['beta_1y'])}, "
                     f"Sharpe 1y {fmt(ind['sharpe_1y'])}, volume z-score {fmt(ind['volume_z_20'], 1)}.")
        if g:
            lines.append(f"Options (30 days, at the money, model): implied vol {fmt_pct(iv, 0)}, call delta {g['delta']:.2f}, "
                         f"gamma {g['gamma']:.4f}, theta {g['theta']:.3f} per day, vega {g['vega']:.3f}.")
        c = self.f._facts(alias)
        if c is not None:
            v = self.f._valoracion(alias, c, d)
            q = c.periodos(d, "revenue", (80, 100), 8)
            crec = q[0].val / q[4].val - 1 if len(q) >= 5 and q[4].val else None
            lines.append(f"Company numbers: P/E {fmt(v['per'], 1)}, price/sales {fmt(v['precio_ventas'], 1)}, "
                         f"price/book {fmt(v['precio_valor_contable'], 1)}, net margin {fmt_pct(v['margen_neto'], 0)}, "
                         f"ROE {fmt_pct(v['roe'], 0)}, debt/equity {fmt(v['deuda_fondos_propios'])}, "
                         f"free cash flow yield {fmt_pct(v['rent_fcf'])}, revenue growth y/y {fmt_pct(crec, 0)}.")
            prox = c.proxima_publicacion(d)
            if prox:
                lines.append(f"Next earnings report in about {(prox - d).days} days.")
        else:
            lines.append("Fund or company without published accounts.")
        lines.append(f"Market (all listed stocks): 20d {fmt_pct(market['mom_20'])}, 60d {fmt_pct(market['mom_60'])}, "
                     f"stocks above their SMA50 {market['breadth']:.0%}.")
        eq = self.equity()
        pos = self._pos_eur(alias)
        p = self.a.positions.get(alias)
        if pos > 0:
            pl = px / p.avg - 1 if p.avg else 0.0
            held = (d - self.held_since.get(alias, d)).days
            lines.append(f"Your position: worth {pos:.2f} ({pos / eq:.0%} of equity), result {fmt_pct(pl)}, held {held} days.")
        else:
            lines.append("Your position: none.")
        cash = self.w.balance() / 100
        lines.append(f"Account: cash {cash:.2f}, equity {eq:.2f} (started with {self.initial:.2f}), "
                     f"hosting costs about {self.daily_host:.2f} a day, cash lasts {cash / self.daily_host:.0f} days. "
                     f"Every trade pays a fee of at least 1.00.")
        return "\n".join(lines)

    def market_summary(self, d: date) -> dict:
        m20, m60, above = [], [], []
        for real in self.mask.real_symbols:
            C = [b.close for b in self._bars(real, d, 70)]
            if len(C) >= 61:
                m20.append(C[-1] / C[-21] - 1); m60.append(C[-1] / C[-61] - 1)
                above.append(C[-1] > sum(C[-50:]) / 50)
        mean = lambda x: sum(x) / len(x) if x else None
        return {"mom_20": mean(m20), "mom_60": mean(m60), "breadth": mean([float(a) for a in above]) or 0.0}

    # ---- ejecución (el "cuerpo") ------------------------------------------------------
    def _buy_amount(self) -> float:
        cash = self.w.balance() / 100
        free = cash - RESERVE_DAYS * self.daily_host
        amt = min(free, max(self.buy_frac * self.equity(), MIN_TICKET_EUR))
        return amt if amt >= MIN_TICKET_EUR else 0.0

    def _order(self, alias: str, side: str, qty: float) -> bool:
        before = self.w.balance()
        st, body = self.a.place_order({"symbol": alias, "qty": qty, "side": side, "type": "market"})
        if st == 200 and body.get("status") == "filled":
            self.trades += 1
            self.fees_cents += to_cents(float(body.get("commission", 0)))
            return True
        return False

    def execute(self, pending: list[tuple[str, int, float]], d: date) -> None:
        """Órdenes decididas ayer: primero ventas (liberan caja), luego compras por convicción."""
        for alias, act, _ in pending:
            if ACTIONS[act] == "sell":
                p = self.a.positions.get(alias)
                if p and p.qty > 0:
                    if self._order(alias, "sell", p.qty):
                        self.held_since.pop(alias, None)
        for alias, act, conv in sorted(pending, key=lambda x: -x[2]):
            if ACTIONS[act] != "buy":
                continue
            amt = self._buy_amount()
            px = self.a._masked_price(alias)
            if amt <= 0 or not px:
                break
            qty = math.floor(amt / (px * (1 + SPREAD_BPS / 1e4)) * 1e4) / 1e4
            fee = self.a._fees_cents("buy", qty, qty * px) / 100
            qty = math.floor((amt - fee) / (px * (1 + SPREAD_BPS / 1e4)) * 1e4) / 1e4
            if qty > 0 and self._order(alias, "buy", qty):
                self.held_since.setdefault(alias, d)

    # ---- etiqueta retrospectiva (solo para entrenar; nunca entra en el estado) -------
    def label(self, dec: Decision) -> Optional[np.ndarray]:
        s = self.md.series[dec.real]
        i = s.index_of(dec.day)
        if i < 0 or i + 1 + LABEL_H >= len(s.bars):
            return None
        c1, c2 = s.bars[i + 1].close, s.bars[i + 1 + LABEL_H].close
        R = c2 / c1 - 1
        C = [b.close for b in s.bars[max(0, i - 60): i + 1]]
        sig = (quant.realized_vol(C, 60) or 0.3) * math.sqrt(LABEL_H / 252)
        E = max(dec.equity_eur, 1e-6)
        spread = SPREAD_BPS / 1e4
        P, A = dec.pos_eur, dec.buy_eur

        def fee(side: str, eur: float) -> float:
            return self.a._fees_cents(side, eur / 100.0, eur) / 100 if eur > 0 else 0.0

        u_hold = (P / E) * R - RISK_AVERSION * (P / E) ** 2 * sig ** 2
        u_sell = -(fee("sell", P) + spread * P) / E if P > 0 else u_hold
        if A > 0:
            u_buy = ((P + A) / E) * R - RISK_AVERSION * ((P + A) / E) ** 2 * sig ** 2 - (fee("buy", A) + spread * A) / E
        else:
            u_buy = u_hold                                  # sin caja libre, comprar = no hacer nada
        return np.array([u_sell, u_hold, u_buy])

    # ---- la vida entera ------------------------------------------------------------------
    def run(self, decide: Callable[[list[str]], np.ndarray], explore: float = 0.0,
            rng: Optional[np.random.Generator] = None, fixed: Optional[str] = None) -> LifeResult:
        """decide(states) -> probs [n, 3]. explore>0 muestrea (entrenamiento); 0 = codicioso.
        fixed = política de referencia sin Laya: 'cash' (no invertir) o 'buyhold' (todo a partes
        iguales el primer día y no tocar)."""
        rng = rng or np.random.default_rng(0)
        pending: list[tuple[str, int, float]] = []
        decisions: list[Decision] = []
        curve = []
        for n, d in enumerate(self.sessions):
            self.w.advance_to(datetime.combine(d, time(15, 0), UTC))
            if not self.w.alive:
                break
            self.execute(pending, d)
            pending = []
            curve.append(round(self.equity(), 2))
            if n == len(self.sessions) - 1 or n % self.decide_every:
                continue
            if fixed == "cash":
                continue
            if fixed == "buyhold":
                if n == 0:
                    k = len(self.mask.aliases)
                    self.buy_frac = 1.0 / k
                    pending = [(a, ACTIONS.index("buy"), 1.0) for a in self.mask.aliases]
                continue
            market = self.market_summary(d)
            aliases, states = [], []
            for alias in self.mask.aliases:
                st = self.state_text(alias, d, n, market)
                if st:
                    aliases.append(alias); states.append(st)
            if not states:
                continue
            probs = decide(states)
            buy_amt = self._buy_amount()
            eq = self.equity()
            for alias, st, p in zip(aliases, states, probs):
                if explore > 0:
                    q = (1 - explore) * p + explore / len(ACTIONS)
                    act = int(rng.choice(len(ACTIONS), p=q / q.sum()))
                else:
                    act = int(np.argmax(p))
                dec = Decision(alias, self.mask.to_real(alias), d, st, p, act, self._pos_eur(alias), eq, buy_amt)
                decisions.append(dec)
                if ACTIONS[act] != "hold":
                    pending.append((alias, act, float(p[act])))
        # cierre: ¿sigue viva al final del horizonte?
        if self.w.alive and self.sessions:
            self.w.advance_to(datetime.combine(self.sessions[-1], time(21, 0), UTC))
        final = self.equity() if self.w.alive else 0.0
        for dec in decisions:
            dec.utils = self.label(dec)
        return LifeResult(self.seed, self.mask.start_day, len(curve), self.w.alive, final, self.initial,
                          self.trades, self.fees_cents / 100, decisions, curve)
