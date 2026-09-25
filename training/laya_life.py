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
from econosim.market.fundamentals import TRIM, Fundamentals                 # noqa: E402
from econosim.market.mask import EpisodeMask                          # noqa: E402
from econosim.twins.alpaca import SPREAD_BPS, AlpacaTwin              # noqa: E402
from econosim.twins.fundamentals_api import FundamentalsTwin          # noqa: E402
from econosim.twins.hetzner import HetznerTwin                        # noqa: E402
from econosim.world import World                                      # noqa: E402

from laya_brain import ACTIONS                                        # noqa: E402

BENCH = "SPY"                  # referencia interna para la beta (la IA no la ve como tal)
INVERSE_ETFS = {"SH", "PSQ", "DOG", "RWM"}   # ETF inversos: la forma real de apostar a la baja con 50 €
HISTORY = 300                  # barras que miran los indicadores
LABEL_H = 20                   # horizonte de la etiqueta (sesiones)
RISK_AVERSION = 1.0            # penalización media-varianza de la etiqueta
MIN_TICKET_EUR = 5.0           # por debajo, la comisión mínima se come la operación
RESERVE_DAYS = 30              # las compras nunca dejan la caja por debajo de 30 días de hosting
TREASURY_DAYS = 7              # si la caja no cubre 7 días de hosting, se vende para rehacer la reserva


def fmt_pct(x: Optional[float], nd: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_vol(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


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
    mandate_buys: int = 0          # compras que puso el mandato porque Laya no llegaba al mínimo

    @property
    def score(self) -> float:
        """Lo que cuenta: el patrimonio con el que acaba. Muerta = 0."""
        return self.final_equity if self.alive else 0.0


class LayaLife:
    def __init__(self, md: MarketData, fund: Fundamentals, seed: str, start_day: date,
                 sessions: int = 126, initial_eur: float = 50.0, decide_every: int = 5,
                 buy_frac: float = 0.25, min_invested: float = 0.0, idle_penalty: float = 0.0,
                 level: int = 3, label_h: int = LABEL_H):
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
        # FORZAR A INVERTIR (petición del usuario, fase 14):
        #  * min_invested: mandato del cuerpo, como un fondo: al menos esta fracción del
        #    patrimonio invertida. Si Laya no compra bastante, compra lo que ELLA puntúa más
        #    alto para "buy". No toca las etiquetas: Laya aprende de lo que habría ganado.
        #  * idle_penalty: coste de oportunidad del efectivo en la ETIQUETA (fracción por
        #    horizonte de etiqueta): el rival del dinero parado es el mercado, no la nada.
        self.min_invested = min_invested
        # CURRÍCULO: `level` = cuánta información ve Laya (1 tendencia, 2 + osciladores y
        # riesgo, 3 todo: opciones, cuentas, mercado); `label_h` = a cuántas sesiones vista
        # se le corrige (120 = largo plazo: comprar y aguantar lo que sube se ve claro).
        self.level = level
        self.label_h = label_h
        self.idle_penalty = idle_penalty
        self.mandate_buys = 0
        self.mandate_keeps = 0
        self.curve: list = []
        self.daily_host = (self.h.types["cx23"]["monthly"] + self.h.pricing["primary_ipv4_monthly"]) / 30.4
        self.trades = 0
        self.fees_cents = 0
        self.held_since: dict[str, date] = {}
        self.forced_sales = 0

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
        # Formato telegráfico: el coste de Laya crece con los tokens (~370 -> ~200) y la
        # cabeza se afina sobre este formato, así que no necesita frases. Porcentajes sin '%'.
        P = lambda x: "na" if x is None else f"{x * 100:+.1f}"
        U = lambda x: "na" if x is None else f"{x * 100:.0f}"
        rsi, adx_ = ind["rsi_14"], ind["adx_14"]
        lines = [f"{alias} day {day_n}",
                 f"mom 5d {P(ind['mom_5'])} 20d {P(ind['mom_20'])} 60d {P(ind['mom_60'])} 120d {P(ind['mom_120'])} "
                 f"1y {P(ind['mom_250'])} vsSMA50 {P(px / ind['sma_50'] - 1 if ind['sma_50'] else None)} "
                 f"vsSMA200 {P(px / ind['sma_200'] - 1 if ind['sma_200'] else None)}"]
        if self.level >= 2:
            lines += [f"RSI {fmt(rsi, 0)} MACDh {fmt((m.get('hist') or 0) / px * 100, 2)} %B {fmt(bb.get('pct_b'))} "
                      f"W%R {fmt(ind['williams_r_14'], 0)} ADX {fmt(adx_, 0)}",
                      f"vol20 {U(ind['vol_20'])} vol60 {U(ind['vol_60'])} ATR {fmt((ind['atr_pct'] or 0) * 100, 1)} "
                      f"offHigh {P(ind['from_high_52w'])} MDD {U(ind['max_drawdown_1y'])} beta {fmt(ind['beta_1y'], 1)} "
                      f"sharpe {fmt(ind['sharpe_1y'], 1)} volZ {fmt(ind['volume_z_20'], 1)}"]
        if self.level < 3:
            return self._tail(alias, d, px, lines, P)
        if g:
            lines.append(f"opt30 IV {U(iv)} delta {g['delta']:.2f} gamma {g['gamma'] * px:.2f} "
                         f"theta {g['theta'] / px * 100:.3f} vega {g['vega'] / px * 100:.2f}")
        c = self.f._facts(alias)
        if c is not None:
            v = self.f._valoracion(alias, c, d)
            q = c.periodos(d, "revenue", TRIM, 8)
            crec = q[0].val / q[4].val - 1 if len(q) >= 5 and q[4].val else None
            prox = c.proxima_publicacion(d)
            lines.append(f"PE {fmt(v['per'], 0)} PS {fmt(v['precio_ventas'], 1)} PB {fmt(v['precio_valor_contable'], 1)} "
                         f"margin {U(v['margen_neto'])} ROE {U(v['roe'])} DE {fmt(v['deuda_fondos_propios'], 1)} "
                         f"FCFy {P(v['rent_fcf'])} revG {P(crec)} earningsIn {(prox - d).days if prox and (prox - d).days <= 120 else 'na'}d")
        else:
            lines.append("fund, no accounts")
        lines.append(f"market 20d {P(market['mom_20'])} 60d {P(market['mom_60'])} breadth {market['breadth'] * 100:.0f}")
        return self._tail(alias, d, px, lines, P)

    def _tail(self, alias: str, d: date, px: float, lines: list, P) -> str:
        """Posición y caja: las ve en todos los niveles (sin ellas no sabe qué puede hacer)."""
        eq = self.equity()
        pos = self._pos_eur(alias)
        p = self.a.positions.get(alias)
        if pos > 0:
            pl = px / p.avg - 1 if p.avg else 0.0
            held = (d - self.held_since.get(alias, d)).days
            lines.append(f"position {pos / eq * 100:.0f} of equity, P/L {P(pl)}, held {held}d")
        else:
            lines.append("position none")
        cash = self.w.balance() / 100
        lines.append(f"cash {cash:.2f} equity {eq:.2f} start {self.initial:.0f} runway {cash / self.daily_host:.0f}d "
                     f"fee 1% min 1.00 max 1%")
        return "\n".join(lines)

    def market_summary(self, d: date) -> dict:
        m20, m60, above = [], [], []
        for real in self.mask.real_symbols:
            if real in INVERSE_ETFS:            # se mueven al revés: falsearían "el mercado"
                continue
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

    def treasury(self, d: date) -> None:
        """Pagar las facturas va antes que cualquier tesis: si la caja no cubre una semana de
        hosting, se vende (la posición más grande primero) hasta rehacer 30 días de reserva."""
        need = RESERVE_DAYS * self.daily_host
        if self.w.balance() / 100 >= TREASURY_DAYS * self.daily_host:
            return
        for alias in sorted(self.a.positions, key=lambda a: -self._pos_eur(a)):
            p = self.a.positions[alias]
            px = self.a._masked_price(alias)
            if not p or p.qty <= 0 or not px:
                continue
            falta = need - self.w.balance() / 100
            if falta <= 0:
                break
            qty = p.qty if falta + 2 >= p.qty * px * 0.8 else math.ceil((falta + 2) / px * 1e4) / 1e4
            if self._order(alias, "sell", min(qty, p.qty)):
                self.forced_sales += 1
                if self.a.positions[alias].qty <= 1e-9:
                    self.held_since.pop(alias, None)

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

    def _mandate(self, pending: list, aliases: list, probs: np.ndarray, eq: float) -> None:
        """Mínimo invertido, como el mandato de un fondo, con el menor movimiento posible:
        1) si las ventas dejarían la cartera por debajo del mínimo, se CONSERVAN las posiciones
           que Laya quiere vender con menos convicción (rotar cuesta comisión y horquilla);
        2) si aun así falta, se compran las favoritas de Laya para "buy"."""
        if self.min_invested <= 0 or eq <= 0:
            return
        buy, sell = ACTIONS.index("buy"), ACTIONS.index("sell")
        # "sell" sin posición es "quedarse fuera": no cuenta como venta ni bloquea compras
        ventas = [(a, c) for a, act, c in pending if act == sell and self._pos_eur(a) > 0]
        vendidas = {a for a, _ in ventas}
        invertido = sum(self._pos_eur(a) for a in self.a.positions if a not in vendidas) / eq
        compras = {a for a, act, _ in pending if act == buy}
        invertido += len(compras) * self.buy_frac
        for a, _c in sorted(ventas, key=lambda x: x[1]):          # la venta menos convencida primero
            if invertido >= self.min_invested:
                break
            pending[:] = [x for x in pending if not (x[0] == a and x[1] == sell)]
            vendidas.discard(a)
            invertido += self._pos_eur(a) / eq
            self.mandate_keeps += 1
        for i in np.argsort(-probs[:, buy]):
            if invertido >= self.min_invested:
                break
            a = aliases[i]
            if a in compras or a in vendidas or self._pos_eur(a) > 0:
                continue
            pending.append((a, buy, float(probs[i, buy])))
            compras.add(a)
            invertido += self.buy_frac
            self.mandate_buys += 1

    # ---- etiqueta retrospectiva (solo para entrenar; nunca entra en el estado) -------
    def label(self, dec: Decision) -> Optional[np.ndarray]:
        s = self.md.series[dec.real]
        i = s.index_of(dec.day)
        H = self.label_h
        if i < 0 or i + 1 + H >= len(s.bars):
            return None
        c1, c2 = s.bars[i + 1].close, s.bars[i + 1 + H].close
        R = c2 / c1 - 1
        C = [b.close for b in s.bars[max(0, i - 60): i + 1]]
        sig = (quant.realized_vol(C, 60) or 0.3) * math.sqrt(H / 252)
        E = max(dec.equity_eur, 1e-6)
        spread = SPREAD_BPS / 1e4
        P, A = dec.pos_eur, dec.buy_eur

        def fee(side: str, eur: float) -> float:
            return self.a._fees_cents(side, eur / 100.0, eur) / 100 if eur > 0 else 0.0

        k = self.idle_penalty                                # coste de oportunidad del efectivo
        u_hold = (P / E) * R - RISK_AVERSION * (P / E) ** 2 * sig ** 2 - k * A / E
        u_sell = (-(fee("sell", P) + spread * P) / E - k * (A + P) / E) if P > 0 else u_hold
        if A > 0:
            u_buy = ((P + A) / E) * R - RISK_AVERSION * ((P + A) / E) ** 2 * sig ** 2 - (fee("buy", A) + spread * A) / E
        else:
            u_buy = u_hold                                  # sin caja libre, comprar = no hacer nada
        return np.array([u_sell, u_hold, u_buy])

    # ---- la vida entera ------------------------------------------------------------------
    def snapshot(self, n: int) -> dict:
        """Lo que el panel enseña de esta vida (para el humano: puede llevar la fecha real)."""
        eq = self.equity()
        pos = []
        for a, p in self.a.positions.items():
            v = self._pos_eur(a)
            px = self.a._masked_price(a)
            if v > 0:
                pos.append({"alias": a, "value": round(v, 2), "pl": round(px / p.avg - 1, 4) if p.avg and px else 0.0,
                            "since": str(self.held_since.get(a, ""))})
        pos.sort(key=lambda x: -x["value"])
        return {"seed": self.seed, "start": str(self.mask.start_day), "day": n, "total": max(0, len(self.sessions) - 1),
                "date": str(self.sessions[min(n, len(self.sessions) - 1)]) if self.sessions else "",
                "equity": round(eq, 2), "cash": round(self.w.balance() / 100, 2), "initial": self.initial,
                "trades": self.trades, "fees": round(self.fees_cents / 100, 2), "mandate_buys": self.mandate_buys,
                "mandate_keeps": self.mandate_keeps,
                "alive": self.w.alive, "positions": pos}

    def run(self, decide: Callable[[list[str]], np.ndarray], explore: float = 0.0,
            rng: Optional[np.random.Generator] = None, fixed: Optional[str] = None,
            on_step: Optional[Callable] = None, key_policy: Optional[Callable] = None) -> LifeResult:
        """decide(states) -> probs [n, 3]. explore>0 muestrea (entrenamiento); 0 = codicioso.
        fixed = política de referencia sin Laya: 'cash' (no invertir) o 'index' (todo al fondo
        del índice el primer día y no tocarlo)."""
        rng = rng or np.random.default_rng(0)
        pending: list[tuple[str, int, float]] = []
        decisions: list[Decision] = []
        curve = []
        for n, d in enumerate(self.sessions):
            self.w.advance_to(datetime.combine(d, time(15, 0), UTC))
            if not self.w.alive:
                break
            self.execute(pending, d)
            self.treasury(d)
            pending = []
            curve.append(round(self.equity(), 2))
            self.curve = curve
            if n == len(self.sessions) - 1 or n % self.decide_every:
                continue
            if fixed == "cash":
                continue
            if fixed == "index":
                # la jugada pasiva obvia: todo el dinero libre al fondo del índice y no tocarlo
                if n == 0:
                    self.buy_frac = 1.0
                    pending = [(self.mask.to_alias(BENCH), ACTIONS.index("buy"), 1.0)]
                continue
            market = self.market_summary(d)
            aliases, states = [], []
            for alias in self.mask.aliases:
                st = self.state_text(alias, d, n, market)
                if st:
                    aliases.append(alias); states.append(st)
            if not states:
                continue
            if key_policy is not None:
                # política por (símbolo real, día, ¿tiene posición?): la usa la evolución para
                # comprobar en el mundo real a la campeona que eligió el simulador rápido
                probs = np.array([key_policy(self.mask.to_real(a), d, self._pos_eur(a) > 0) for a in aliases])
            else:
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
            antes = {(a, act) for a, act, _ in pending}
            self._mandate(pending, aliases, probs, eq)
            if on_step:
                despues = {(a, act) for a, act, _ in pending}
                por_alias = {a: p for a, p in zip(aliases, probs)}
                elegido = {x.alias: x.action for x in decisions[-len(aliases):]}
                sell, buy = ACTIONS.index("sell"), ACTIONS.index("buy")
                lote = []
                for a in aliases:
                    d = {"alias": a, "act": ACTIONS[elegido[a]], "p": [round(float(v), 3) for v in por_alias[a]],
                         "mandate": False, "held": self._pos_eur(a) > 0}
                    if (a, sell) in antes and (a, sell) not in despues:
                        d["kept"] = True                          # el mandato la conservó
                    lote.append(d)
                for a, act in despues - antes:
                    if act == buy:
                        lote.append({"alias": a, "act": "buy", "p": [round(float(v), 3) for v in por_alias[a]],
                                     "mandate": True, "held": False})
                on_step(self, n, lote)
        # cierre: ¿sigue viva al final del horizonte?
        if self.w.alive and self.sessions:
            self.w.advance_to(datetime.combine(self.sessions[-1], time(21, 0), UTC))
        final = self.equity() if self.w.alive else 0.0
        if on_step:
            on_step(self, len(curve) - 1, None)
        for dec in decisions:
            dec.utils = self.label(dec)
        return LifeResult(self.seed, self.mask.start_day, len(curve), self.w.alive, final, self.initial,
                          self.trades, self.fees_cents / 100, decisions, curve, self.mandate_buys)
