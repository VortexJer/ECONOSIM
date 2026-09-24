"""Opciones sobre acciones del gemelo de Alpaca (docs.alpaca.markets, Options API).

Endpoints reales que replica:
  * Trading  GET  /v2/options/contracts?underlying_symbols=X[&type=&expiration_date_gte=&expiration_date_lte=&strike_price_gte=&strike_price_lte=&limit=]
  * Trading  GET  /v2/options/contracts/{symbol_or_id}
  * Data     GET  /v1beta1/options/snapshots/{underlying}   (cotización + griegas + volatilidad implícita)
  * Trading  POST /v2/orders con el símbolo OCC del contrato (compra para abrir / venta para cerrar)
  * Trading  GET  /v2/positions (las posiciones de opciones salen con asset_class "us_option")

Reglas, como una cuenta real de nivel 2 (opciones compradas):
  * Solo se COMPRA para abrir y se VENDE lo que se tiene; vender en descubierto se
    rechaza igual que a una cuenta sin permiso para opciones no cubiertas.
  * 1 contrato = 100 unidades del subyacente (multiplicador real).
  * Vencimientos mensuales (tercer viernes) de los tres próximos meses; strikes en una
    rejilla fija alrededor del precio del día en que se listan (no se mueven después).
  * Precio = Black-Scholes con insumos reales del día (quant.py): ES UNA ESTIMACIÓN DE
    MODELO, no una cotización histórica, y la API lo declara en cada instantánea.
  * Horquilla: medio-spread del 1,5 % del precio (mínimo 0,01), típico de una opción
    líquida cerca del dinero. Comisión del bróker de referencia: 0,65 por contrato,
    mínimo 1,00 (Alpaca no la cobra; el sim es más estricto a propósito, §2.7).
  * Al vencimiento las opciones dentro del dinero se liquidan por diferencias al
    intrínseco del cierre; las demás expiran a cero. (Fuera se entregarían acciones;
    liquidar en efectivo evita exigir a la IA el capital de 100 acciones.)
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from math import ceil as _ceil
from typing import TYPE_CHECKING, Optional

from aiohttp import web

from ..ledger import to_cents
from ..market import quant
from ..market.calendar import MARKET_CLOSE, UTC

if TYPE_CHECKING:
    from .alpaca import AlpacaTwin

MULTIPLIER = 100
HALF_SPREAD_PCT = 0.015
MIN_TICK = 0.01
COMMISSION_PER_CONTRACT = 0.65
COMMISSION_MIN = 1.00
MONTHS_LISTED = 3
STRIKE_RANGE = 0.25          # strikes listados: ±25 % del precio del día de listado
BENCH = "SPY"                # referencia de la prima de riesgo de varianza (solo motor)


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _step(px: float) -> float:
    return 1.0 if px < 50 else 2.5 if px < 200 else 5.0


class Contract:
    def __init__(self, occ: str, alias: str, kind: str, strike: float, expiry: date):
        self.occ, self.alias, self.kind, self.strike, self.expiry = occ, alias, kind, strike, expiry
        self.id = "00000000-0000-4000-9000-" + secrets.token_hex(6)


class OptPosition:
    def __init__(self, occ: str, qty: int, avg: float):
        self.occ, self.qty, self.avg = occ, qty, avg      # avg = prima por acción, escala IA


class OptionsDesk:
    def __init__(self, twin: "AlpacaTwin"):
        self.t = twin
        self.contracts: dict[str, Contract] = {}          # occ -> contrato
        self.listed: dict[tuple[str, date], bool] = {}    # (alias, vencimiento) ya listado
        self.positions: dict[str, OptPosition] = {}
        self.settled: list[dict] = []

    # ---- símbolos --------------------------------------------------------
    def _root(self, alias: str) -> str:
        return alias.replace("-", "")

    def _display_date(self, d: date) -> date:
        return self.t.clock.display(datetime.combine(d, MARKET_CLOSE, UTC)).date()

    def occ_symbol(self, alias: str, kind: str, strike: float, expiry: date) -> str:
        dd = self._display_date(expiry)
        return f"{self._root(alias)}{dd:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"

    def is_option(self, symbol: str) -> bool:
        self.settle()
        return symbol in self.contracts

    # ---- insumos de valoración (solo barras <= hoy) ------------------------
    def _today(self) -> Optional[date]:
        return self.t._session_asof()

    def _closes(self, real: str, d: date, n: int = 300) -> list[float]:
        s = self.t.data.series[real]
        i = s.index_of(d)
        return [b.close for b in s.bars[max(0, i - n + 1): i + 1]] if i >= 0 else []

    def _iv(self, alias: str, d: date) -> Optional[float]:
        real = self.t.mask.to_real(alias)
        bench = BENCH if BENCH in self.t.data.series else real
        return quant.implied_vol(self._closes(real, d, 70), self._closes(bench, d, 40), d)

    def value(self, c: Contract, d: Optional[date] = None) -> Optional[dict]:
        """Precio medio y griegas del contrato en el día d (por acción, escala de la IA)."""
        d = d or self._today()
        S = self.t._masked_price(c.alias)
        if d is None or S is None:
            return None
        iv = self._iv(c.alias, d)
        if iv is None:
            return None
        T = max((c.expiry - d).days, 0) / 365.0
        g = quant.bs(c.kind, S, c.strike, T, quant.macro().risk_free(d), iv)
        g["iv"] = iv
        return g

    # ---- listado ----------------------------------------------------------
    def _expiries(self, d: date) -> list[date]:
        out, y, m = [], d.year, d.month
        while len(out) < MONTHS_LISTED:
            f = _third_friday(y, m)
            if f > d:
                out.append(f)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return out

    def list_for(self, alias: str) -> list[Contract]:
        d = self._today()
        S = self.t._masked_price(alias)
        if d is None or S is None:
            return []
        for exp in self._expiries(d):
            if self.listed.get((alias, exp)):
                continue
            st = _step(S)
            lo, hi = S * (1 - STRIKE_RANGE), S * (1 + STRIKE_RANGE)
            k = _ceil(lo / st) * st
            while k <= hi + 1e-9:
                for kind in ("call", "put"):
                    occ = self.occ_symbol(alias, kind, k, exp)
                    self.contracts.setdefault(occ, Contract(occ, alias, kind, round(k, 2), exp))
                k += st
            self.listed[(alias, exp)] = True
        return sorted((c for c in self.contracts.values() if c.alias == alias and c.expiry >= d),
                      key=lambda c: (c.expiry, c.kind, c.strike))

    # ---- liquidación al vencimiento ---------------------------------------
    def settle(self) -> None:
        d = self._today()
        now = self.t.clock.real_now()
        if d is None:
            return
        for occ, pos in list(self.positions.items()):
            c = self.contracts[occ]
            # vence al cierre de su día; si hoy es el vencimiento, solo tras el cierre
            if c.expiry > d or (c.expiry == d and now < datetime.combine(d, MARKET_CLOSE, UTC)):
                continue
            real = self.t.mask.to_real(c.alias)
            bar = self.t.data.series[real].asof(c.expiry)
            S = self.t.mask.index_price(real, bar.close)
            intr = max(S - c.strike, 0.0) if c.kind == "call" else max(c.strike - S, 0.0)
            cents = to_cents(intr * MULTIPLIER * pos.qty)
            if cents > 0:
                self.t.world.receive(cents, f"Liquidación al vencimiento {pos.qty} {occ}", "Alpaca Securities LLC", ref=occ)
            self.settled.append({"symbol": occ, "qty": pos.qty, "intrinsic": round(intr, 4), "cash": cents / 100})
            del self.positions[occ]

    def market_value_cents(self) -> int:
        self.settle()
        tot = 0
        for occ, pos in self.positions.items():
            v = self.value(self.contracts[occ])
            if v:
                tot += to_cents(max(v["price"] - self._half(v["price"]), 0.0) * MULTIPLIER * pos.qty)
        return tot

    @staticmethod
    def _half(px: float) -> float:
        return max(MIN_TICK, px * HALF_SPREAD_PCT)

    # ---- JSON --------------------------------------------------------------
    def contract_json(self, c: Contract) -> dict:
        return {"id": c.id, "symbol": c.occ, "name": f"{c.alias} {self._display_date(c.expiry):%b %d %Y} "
                f"{c.strike:g} {'Call' if c.kind == 'call' else 'Put'}",
                "status": "active", "tradable": True, "expiration_date": self._display_date(c.expiry).isoformat(),
                "root_symbol": self._root(c.alias), "underlying_symbol": c.alias, "type": c.kind,
                "style": "american", "strike_price": f"{c.strike:g}", "multiplier": str(MULTIPLIER),
                "size": str(MULTIPLIER)}

    def snapshot_json(self, c: Contract) -> Optional[dict]:
        v = self.value(c)
        if v is None:
            return None
        h = self._half(v["price"])
        now = self.t.clock.display_iso()
        return {"latestQuote": {"t": now, "bp": round(max(v["price"] - h, 0.0), 2), "bs": 10,
                                "ap": round(v["price"] + h, 2), "as": 10, "bx": "C", "ax": "C", "c": " "},
                "latestTrade": {"t": now, "p": round(v["price"], 2), "s": 1, "x": "C", "c": " "},
                "greeks": {k: round(v[k], 5) for k in ("delta", "gamma", "theta", "vega", "rho")},
                "impliedVolatility": round(v["iv"], 4),
                "pricingModel": "black_scholes_model_estimate"}

    def position_json(self, pos: OptPosition) -> dict:
        c = self.contracts[pos.occ]
        v = self.value(c) or {"price": 0.0}
        cur = max(v["price"] - self._half(v["price"]), 0.0)
        mv, cost = cur * MULTIPLIER * pos.qty, pos.avg * MULTIPLIER * pos.qty
        return {"asset_id": c.id, "symbol": c.occ, "exchange": "OPRA", "asset_class": "us_option",
                "qty": str(pos.qty), "qty_available": str(pos.qty), "avg_entry_price": f"{pos.avg:.4f}",
                "side": "long", "market_value": f"{mv:.2f}", "cost_basis": f"{cost:.2f}",
                "current_price": f"{cur:.4f}", "lastday_price": f"{cur:.4f}",
                "unrealized_pl": f"{mv-cost:.2f}", "unrealized_plpc": f"{(mv/cost-1) if cost else 0:.6f}",
                "change_today": "0"}

    # ---- órdenes -------------------------------------------------------------
    @staticmethod
    def fees_cents(qty: int) -> int:
        return int(_ceil(max(qty * COMMISSION_PER_CONTRACT, COMMISSION_MIN) * 100 - 1e-9))

    def place(self, occ: str, side: str, qty_raw, body: dict) -> tuple[int, dict]:
        t = self.t
        try:
            qf = float(qty_raw)
        except (TypeError, ValueError):
            return 422, {"code": 42210000, "message": "qty is required"}
        if qf <= 0 or qf != int(qf):
            return 422, {"code": 42210000, "message": "qty must be a positive whole number of contracts"}
        qty = int(qf)
        c = self.contracts[occ]
        d = self._today()
        if d is None or c.expiry < d:
            return 422, {"code": 42210000, "message": "contract is expired"}
        if not t.cal.is_open(t.clock.real_now()):
            return 403, {"code": 40310000, "message": "market is closed"}
        v = self.value(c)
        if v is None:
            return 422, {"code": 42210000, "message": "no quote available for this contract"}
        if t.world.live.block("alpaca", "place_order", {"symbol": occ, "side": side, "qty": qty}):
            now = t.clock.display_iso()
            return 200, {"id": "00000000-0000-4000-8000-" + f"{t._next_id():012d}", "symbol": occ,
                         "asset_class": "us_option", "qty": str(qty), "filled_qty": "0", "side": side,
                         "type": "market", "status": "accepted", "created_at": now, "submitted_at": now,
                         "filled_at": None, "filled_avg_price": None}
        h = self._half(v["price"])
        fill = v["price"] + h if side == "buy" else max(v["price"] - h, 0.0)
        cash = fill * MULTIPLIER * qty
        fee = self.fees_cents(qty)
        if side == "buy":
            if to_cents(cash) + fee > t.world.balance():
                return 403, {"code": 40310000, "message": "insufficient options buying power"}
            t.world.pay(to_cents(cash), f"Compra {qty} {occ} @ {fill:.2f}", "Alpaca Securities LLC", ref=occ)
            t.world.pay(fee, f"Comisión compra {occ}", "Alpaca Securities LLC", ref=occ)
            p = self.positions.get(occ) or OptPosition(occ, 0, fill)
            p.avg = (p.avg * p.qty + fill * qty) / (p.qty + qty)
            p.qty += qty
            self.positions[occ] = p
        else:
            p = self.positions.get(occ)
            if not p or p.qty < qty:
                return 403, {"code": 40310000,
                             "message": "account is not authorized to sell uncovered option contracts"}
            t.world.receive(to_cents(cash), f"Venta {qty} {occ} @ {fill:.2f}", "Alpaca Securities LLC", ref=occ)
            t.world.pay(fee, f"Comisión venta {occ}", "Alpaca Securities LLC", ref=occ)
            p.qty -= qty
            if p.qty == 0:
                del self.positions[occ]
        now = t.clock.display_iso()
        order = {"id": "00000000-0000-4000-8000-" + f"{t._next_id():012d}", "client_order_id": secrets.token_hex(8),
                 "created_at": now, "submitted_at": now, "filled_at": now, "updated_at": now,
                 "symbol": occ, "asset_class": "us_option", "qty": str(qty), "filled_qty": str(qty),
                 "type": "market", "side": side, "time_in_force": body.get("time_in_force", "day"),
                 "status": "filled", "filled_avg_price": f"{fill:.4f}", "commission": f"{fee/100:.2f}",
                 "position_intent": "buy_to_open" if side == "buy" else "sell_to_close",
                 "limit_price": None, "stop_price": None, "order_class": "simple", "asset_id": c.id}
        t.orders[order["id"]] = order
        return 200, order

    # ---- HTTP ----------------------------------------------------------------
    async def h_contracts(self, req: web.Request):
        q = req.query
        unders = [s.strip().upper() for s in q.get("underlying_symbols", "").split(",") if s.strip()]
        if not unders:
            return web.json_response({"code": 42210000, "message": "underlying_symbols is required"}, status=422)
        rows = []
        for a in unders:
            if self.t.mask.to_real(a) is None:
                continue
            rows += self.list_for(a)

        def ok(c: Contract) -> bool:
            e = self._display_date(c.expiry).isoformat()
            if q.get("type") and c.kind != q["type"]:
                return False
            if q.get("expiration_date") and e != q["expiration_date"]:
                return False
            if q.get("expiration_date_gte") and e < q["expiration_date_gte"]:
                return False
            if q.get("expiration_date_lte") and e > q["expiration_date_lte"]:
                return False
            try:
                if q.get("strike_price_gte") and c.strike < float(q["strike_price_gte"]):
                    return False
                if q.get("strike_price_lte") and c.strike > float(q["strike_price_lte"]):
                    return False
            except ValueError:
                return False
            return True

        rows = [c for c in rows if ok(c)]
        limit = min(int(q.get("limit", 100) or 100), 10000)
        return web.json_response({"option_contracts": [self.contract_json(c) for c in rows[:limit]],
                                  "next_page_token": None})

    async def h_contract(self, req: web.Request):
        key = req.match_info["sym"].upper()
        self.settle()
        c = self.contracts.get(key) or next((x for x in self.contracts.values() if x.id == key.lower()), None)
        if c is None:
            return web.json_response({"code": 40410000, "message": "option contract not found"}, status=404)
        return web.json_response(self.contract_json(c))

    async def h_snapshots(self, req: web.Request):
        alias = req.match_info["sym"].upper()
        if self.t.mask.to_real(alias) is None:
            return web.json_response({"code": 40410000, "message": f"underlying {alias} not found"}, status=404)
        q = req.query
        cs = self.list_for(alias)
        if q.get("type"):
            cs = [c for c in cs if c.kind == q["type"]]
        snaps = {}
        for c in cs[: min(int(q.get("limit", 100) or 100), 1000)]:
            s = self.snapshot_json(c)
            if s:
                snaps[c.occ] = s
        return web.json_response({"snapshots": snaps, "next_page_token": None})
