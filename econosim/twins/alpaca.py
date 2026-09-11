"""Gemelo de la Alpaca API (broker + market data v2, https://docs.alpaca.markets).

La IA invierte en bolsa a través de esto. Ve símbolos enmascarados y precios
indexados a 100; por dentro son datos históricos reales en la fecha virtual actual.
Trading API: account, assets, orders, positions. Market Data API: bars, latest
quote/trade, snapshots. Clock y calendar como los reales.

Auth: cabeceras APCA-API-KEY-ID / APCA-API-SECRET-KEY (paper).
Órdenes de mercado al cierre de la barra del día con spread y comisión reales
(comisión 0 como Alpaca; el coste es el spread). Liquidación T+1 del efectivo.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from aiohttp import web

from ..ledger import to_cents
from ..market.calendar import MARKET_CLOSE, MARKET_OPEN, MarketCalendar, UTC
from ..market.data import MarketData
from ..market.mask import EpisodeMask
from ..world import World, BANK

HOST = "api.alpaca.markets"
DATA_HOST = "data.alpaca.markets"
COUNTERPARTY = "Alpaca Securities LLC"
SPREAD_BPS = 5.0          # medio-spread aplicado a favor del mercado (5 pb ≈ acción líquida)
POS_ACCOUNT = "positions"  # cuenta contable del valor de las posiciones (a coste)


def _err(status: int, message: str, code: int = 40010000) -> web.Response:
    return web.json_response({"code": code, "message": message}, status=status)


class Position:
    def __init__(self, alias: str, qty: float, avg_real: float):
        self.alias = alias
        self.qty = qty
        self.avg_real = avg_real     # precio medio de coste, en escala REAL


class AlpacaTwin:
    host = HOST
    data_host = DATA_HOST

    def __init__(self, world: World, data: MarketData, mask: EpisodeMask,
                 key_id: Optional[str] = None, secret: Optional[str] = None):
        self.world = world
        self.clock = world.clock
        self.data = data
        self.mask = mask
        self.cal = MarketCalendar(data.trading_days(mask.real_symbols))
        self.key_id = key_id or "PK" + secrets.token_hex(8).upper()
        self.secret = secret or secrets.token_urlsafe(32)
        self.positions: dict[str, Position] = {}     # alias -> Position
        self.orders: dict[str, dict] = {}
        self._seq = 0
        self.account_number = "PA" + secrets.token_hex(5).upper()
        world.register("alpaca", self)

    # ---- fechas / precios internos --------------------------------------
    def _today_real(self) -> date:
        return self.clock.real_now().date()

    def _session_asof(self) -> Optional[date]:
        return self.cal.session_asof(self._today_real())

    def _real_close(self, real_symbol: str, d: date) -> Optional[float]:
        bar = self.data.series[real_symbol].asof(d)
        return bar.close if bar else None

    def _masked_price(self, alias: str) -> Optional[float]:
        real = self.mask.to_real(alias)
        d = self._session_asof()
        if real is None or d is None:
            return None
        c = self._real_close(real, d)
        return self.mask.index_price(real, c) if c is not None else None

    # ---- valoración de la cartera ---------------------------------------
    def equity_cents(self) -> int:
        cash = self.world.balance()
        mv = 0
        for pos in self.positions.values():
            px = self._masked_price(pos.alias)     # escala enmascarada
            if px is None:
                continue
            real = self.mask.to_real(pos.alias)
            mv += to_cents(pos.qty * self.mask.unindex_price(real, px))   # valor a escala real = dinero de verdad
        return cash + mv

    # ================================================================= HTTP
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_get("/v2/account", self.h_account)
        r.add_get("/v2/assets", self.h_assets)
        r.add_get("/v2/assets/{sym}", self.h_asset)
        r.add_get("/v2/clock", self.h_clock)
        r.add_get("/v2/calendar", self.h_calendar)
        r.add_get("/v2/positions", self.h_positions)
        r.add_get("/v2/positions/{sym}", self.h_position)
        r.add_get("/v2/orders", self.h_orders)
        r.add_get("/v2/orders/{id}", self.h_order)
        r.add_post("/v2/orders", self.h_create_order)
        return app

    def data_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_get("/v2/stocks/{sym}/bars", self.h_bars)
        r.add_get("/v2/stocks/{sym}/bars/latest", self.h_latest_bar)
        r.add_get("/v2/stocks/{sym}/quotes/latest", self.h_latest_quote)
        r.add_get("/v2/stocks/{sym}/trades/latest", self.h_latest_trade)
        r.add_get("/v2/stocks/{sym}/snapshot", self.h_snapshot)
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        if (request.headers.get("APCA-API-KEY-ID") != self.key_id
                or request.headers.get("APCA-API-SECRET-KEY") != self.secret):
            return _err(401, "access key verification failed", 40110000)
        return await handler(request)

    # ---- Trading: cuenta ------------------------------------------------
    async def h_account(self, _):
        cash = self.world.balance()
        eq = self.equity_cents()
        return web.json_response({
            "id": "00000000-0000-4000-8000-" + self.account_number[-12:].lower().rjust(12, "0"),
            "account_number": self.account_number, "status": "ACTIVE", "currency": "USD",
            "cash": f"{cash/100:.2f}", "portfolio_value": f"{eq/100:.2f}", "equity": f"{eq/100:.2f}",
            "buying_power": f"{cash/100:.2f}", "non_marginable_buying_power": f"{cash/100:.2f}",
            "long_market_value": f"{(eq-cash)/100:.2f}", "short_market_value": "0",
            "pattern_day_trader": False, "trading_blocked": False, "account_blocked": False,
            "created_at": self.clock.display_iso(self.mask.start_day and datetime.combine(self.mask.start_day, MARKET_OPEN, UTC)),
            "multiplier": "1", "daytrade_count": 0})

    async def h_assets(self, req):
        assets = [self._asset(a) for a in self.mask.aliases]
        return web.json_response(assets)

    def _asset(self, alias: str) -> dict:
        return {"id": "00000000-0000-4000-8000-" + f"{abs(hash(alias)):012d}"[:12],
                "class": "us_equity", "exchange": "NASDAQ", "symbol": alias, "name": f"{alias} Corp.",
                "status": "active", "tradable": True, "marginable": True, "shortable": True,
                "easy_to_borrow": True, "fractionable": True}

    async def h_asset(self, req):
        alias = req.match_info["sym"].upper()
        if self.mask.to_real(alias) is None:
            return _err(404, f"asset {alias} not found", 40410000)
        return web.json_response(self._asset(alias))

    # ---- Trading: reloj y calendario -----------------------------------
    async def h_clock(self, _):
        now = self.clock.real_now()
        is_open = self.cal.is_open(now)
        return web.json_response({
            "timestamp": self.clock.display_iso(now),
            "is_open": is_open,
            "next_open": self.clock.display_iso(self.cal.next_open(now)),
            "next_close": self.clock.display_iso(self.cal.next_close(now))})

    async def h_calendar(self, req):
        start = req.query.get("start")
        end = req.query.get("end")
        # fechas de query llegan en calendario MOSTRADO -> a real restando el offset de años
        off = self.clock.offset_years
        def to_real(s):
            d = datetime.strptime(s, "%Y-%m-%d").date()
            return d.replace(year=d.year - off)
        days = self.cal._days
        if start:
            days = [d for d in days if d >= to_real(start)]
        if end:
            days = [d for d in days if d <= to_real(end)]
        out = []
        for d in days[:5000]:
            disp = d.replace(year=d.year + off)
            out.append({"date": disp.isoformat(), "open": "09:30", "close": "16:00",
                        "session_open": "0930", "session_close": "1600"})
        return web.json_response(out)

    # ---- Market Data: barras y cotizaciones ----------------------------
    def _display_dt(self, d: date, t) -> str:
        return self.clock.display_iso(datetime.combine(d, t, UTC))

    async def h_bars(self, req):
        alias = req.match_info["sym"].upper()
        s = self.mask.series_for(alias)
        if s is None:
            return _err(404, f"asset {alias} not found", 40410000)
        real = self.mask.to_real(alias)
        today = self._session_asof()
        limit = min(int(req.query.get("limit", 1000)), 10000)
        # solo barras <= hoy virtual (nunca el futuro), las más recientes
        past = [b for b in s.bars if today is not None and b.day <= today]
        past = past[-limit:]
        bars = [{"t": self._display_dt(b.day, MARKET_OPEN), "o": round(self.mask.index_price(real, b.open), 4),
                 "h": round(self.mask.index_price(real, b.high), 4), "l": round(self.mask.index_price(real, b.low), 4),
                 "c": round(self.mask.index_price(real, b.close), 4), "v": b.volume, "n": max(1, b.volume // 100),
                 "vw": round(self.mask.index_price(real, (b.high + b.low + b.close) / 3), 4)} for b in past]
        return web.json_response({"bars": bars, "symbol": alias, "next_page_token": None})

    async def h_latest_bar(self, req):
        alias = req.match_info["sym"].upper()
        s = self.mask.series_for(alias)
        today = self._session_asof()
        if s is None or today is None:
            return _err(404, f"asset {alias} not found", 40410000)
        real = self.mask.to_real(alias)
        b = s.asof(today)
        bar = {"t": self._display_dt(b.day, MARKET_OPEN), "o": round(self.mask.index_price(real, b.open), 4),
               "h": round(self.mask.index_price(real, b.high), 4), "l": round(self.mask.index_price(real, b.low), 4),
               "c": round(self.mask.index_price(real, b.close), 4), "v": b.volume, "n": max(1, b.volume // 100),
               "vw": round(self.mask.index_price(real, b.close), 4)}
        return web.json_response({"bar": bar, "symbol": alias})

    def _quote(self, alias: str):
        px = self._masked_price(alias)
        if px is None:
            return None
        half = px * SPREAD_BPS / 1e4
        return round(px - half, 4), round(px + half, 4)

    async def h_latest_quote(self, req):
        alias = req.match_info["sym"].upper()
        q = self._quote(alias)
        if q is None:
            return _err(404, f"asset {alias} not found", 40410000)
        bid, ask = q
        now = self.clock.display_iso()
        return web.json_response({"symbol": alias, "quote": {
            "t": now, "bp": bid, "bs": 100, "ap": ask, "as": 100, "bx": "V", "ax": "V", "c": ["R"], "z": "C"}})

    async def h_latest_trade(self, req):
        alias = req.match_info["sym"].upper()
        px = self._masked_price(alias)
        if px is None:
            return _err(404, f"asset {alias} not found", 40410000)
        return web.json_response({"symbol": alias, "trade": {
            "t": self.clock.display_iso(), "p": round(px, 4), "s": 100, "x": "V", "i": self._next_id(), "z": "C", "c": []}})

    async def h_snapshot(self, req):
        alias = req.match_info["sym"].upper()
        s = self.mask.series_for(alias)
        today = self._session_asof()
        if s is None or today is None:
            return _err(404, f"asset {alias} not found", 40410000)
        lt = (await self.h_latest_trade(req))
        lq = (await self.h_latest_quote(req))
        lb = (await self.h_latest_bar(req))
        import json as _j
        return web.json_response({"symbol": alias,
                                  "latestTrade": _j.loads(lt.text)["trade"],
                                  "latestQuote": _j.loads(lq.text)["quote"],
                                  "minuteBar": _j.loads(lb.text)["bar"],
                                  "dailyBar": _j.loads(lb.text)["bar"]})

    # ---- Trading: posiciones -------------------------------------------
    def _position_json(self, pos: Position) -> dict:
        real = self.mask.to_real(pos.alias)
        cur_mask = self._masked_price(pos.alias) or 0.0
        avg_mask = self.mask.index_price(real, pos.avg_real)
        mv = pos.qty * cur_mask
        cost = pos.qty * avg_mask
        return {"asset_id": self._asset(pos.alias)["id"], "symbol": pos.alias, "exchange": "NASDAQ",
                "asset_class": "us_equity", "qty": f"{pos.qty:g}", "qty_available": f"{pos.qty:g}",
                "avg_entry_price": f"{avg_mask:.4f}", "side": "long",
                "market_value": f"{mv:.2f}", "cost_basis": f"{cost:.2f}",
                "current_price": f"{cur_mask:.4f}", "lastday_price": f"{cur_mask:.4f}",
                "unrealized_pl": f"{mv-cost:.2f}", "unrealized_plpc": f"{(mv/cost-1) if cost else 0:.6f}",
                "change_today": "0"}

    async def h_positions(self, _):
        return web.json_response([self._position_json(p) for p in self.positions.values() if p.qty > 0])

    async def h_position(self, req):
        alias = req.match_info["sym"].upper()
        pos = self.positions.get(alias)
        if not pos or pos.qty <= 0:
            return _err(404, "position does not exist", 40410000)
        return web.json_response(self._position_json(pos))

    # ---- Trading: órdenes ----------------------------------------------
    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    async def h_orders(self, _):
        return web.json_response(list(self.orders.values())[-500:])

    async def h_order(self, req):
        o = self.orders.get(req.match_info["id"])
        return web.json_response(o) if o else _err(404, "order not found", 40410000)

    async def h_create_order(self, req):
        try:
            body = await req.json()
        except Exception:
            return _err(422, "invalid JSON", 42210000)
        alias = str(body.get("symbol", "")).upper()
        side = body.get("side")
        otype = body.get("type", "market")
        if self.mask.to_real(alias) is None:
            return _err(404, f"asset {alias} not found", 40410000)
        if side not in ("buy", "sell"):
            return _err(422, "side must be buy or sell", 42210000)
        if otype != "market":
            return _err(422, "only market orders are supported", 42210000)
        try:
            qty = float(body.get("qty"))
        except (TypeError, ValueError):
            return _err(422, "qty is required", 42210000)
        if qty <= 0:
            return _err(422, "qty must be positive", 42210000)
        px = self._masked_price(alias)
        if px is None:
            return _err(422, "market is not open for this asset", 42210000)
        if not self.cal.is_open(self.clock.real_now()):
            # Alpaca acepta la orden y la deja pendiente hasta la apertura; para el
            # simulador la rechazamos con el error real de mercado cerrado.
            return _err(403, "market is closed", 40310000)

        real = self.mask.to_real(alias)
        half = px * SPREAD_BPS / 1e4
        fill_mask = px + half if side == "buy" else px - half     # cruzas el spread
        fill_real = self.mask.unindex_price(real, fill_mask)
        cash_delta = qty * fill_real                              # dinero real que mueve

        if side == "buy":
            if to_cents(cash_delta) > self.world.balance():
                return _err(403, "insufficient buying power", 40310000)
            self.world.pay(to_cents(cash_delta), f"Compra {qty:g} {alias} @ {fill_mask:.2f}", COUNTERPARTY, ref=alias)
            pos = self.positions.get(alias) or Position(alias, 0.0, fill_real)
            total_cost = pos.qty * pos.avg_real + qty * fill_real
            pos.qty += qty
            pos.avg_real = total_cost / pos.qty
            self.positions[alias] = pos
        else:
            pos = self.positions.get(alias)
            if not pos or pos.qty < qty:
                return _err(403, "insufficient qty available", 40310000)
            self.world.receive(to_cents(cash_delta), f"Venta {qty:g} {alias} @ {fill_mask:.2f}", COUNTERPARTY, ref=alias)
            pos.qty -= qty

        oid = f"{self._next_id():08d}"
        now = self.clock.display_iso()
        order = {"id": "00000000-0000-4000-8000-" + oid.rjust(12, "0"), "client_order_id": secrets.token_hex(8),
                 "created_at": now, "submitted_at": now, "filled_at": now, "updated_at": now,
                 "symbol": alias, "asset_class": "us_equity", "qty": f"{qty:g}", "filled_qty": f"{qty:g}",
                 "type": "market", "side": side, "time_in_force": body.get("time_in_force", "day"),
                 "status": "filled", "filled_avg_price": f"{fill_mask:.4f}",
                 "limit_price": None, "stop_price": None, "order_class": "simple",
                 "asset_id": self._asset(alias)["id"]}
        self.orders[order["id"]] = order
        return web.json_response(order, status=200)
