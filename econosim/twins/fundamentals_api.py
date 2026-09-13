"""Gemelo de Financial Modeling Prep (financialmodelingprep.com): los números de las empresas.

Es lo que un inversor mira ANTES de comprar, y aquí está tal cual: cuenta de resultados,
balance, flujo de caja, ratios de valoración, calendario de resultados con estimaciones y
sorpresas, y consenso de analistas. Datos REALES de los informes presentados al supervisor.

Tres reglas que lo hacen honesto:

1. **Nada antes de tiempo.** Cada cifra lleva su fecha de publicación y solo se sirve si
   ya se había publicado el día virtual. Un trimestre cerrado el 30 de junio no existe
   hasta que se presenta en agosto. Sin esto la IA aprendería a adivinar con datos del
   futuro y en la vida real perdería el dinero.
2. **Los importes van al mismo factor que el precio.** El PER, el margen, el crecimiento
   y cualquier ratio salen EXACTOS; el tamaño absoluto no delata a la empresa.
3. **Las estimaciones no son el resultado.** El consenso previo se construye con la
   dispersión real de las sorpresas trimestrales, así que acertar la sorpresa es tan
   difícil como fuera.

Auth como el real: `?apikey=` en la query (o cabecera `X-Api-Key`).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from aiohttp import web

from ..market.data import MarketData
from ..market.fundamentals import ANUAL, TRIM, CompanyFacts, Fundamentals
from ..market.mask import EpisodeMask
from ..world import World

HOST = "financialmodelingprep.com"
# Dispersión real de las sorpresas de resultados en grandes valores: la estimación de
# consenso suele quedarse corta y falla en el orden del 5-10 %. Medido sobre el histórico
# de anuncios (ver scripts/study_investor_toolkit.py).
SORPRESA_MEDIA = 0.055
SORPRESA_DISP = 0.085


def _err(status: int, message: str) -> web.Response:
    return web.json_response({"Error Message": message}, status=status)


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(x, n)


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or not b else a / b


class FundamentalsTwin:
    host = HOST

    def __init__(self, world: World, data: MarketData, mask: EpisodeMask,
                 fundamentals: Optional[Fundamentals] = None, api_key: Optional[str] = None):
        self.world = world
        self.clock = world.clock
        self.data = data
        self.mask = mask
        self.fund = fundamentals if fundamentals is not None else Fundamentals()
        self.api_key = api_key or secrets.token_hex(16)
        self.calls = 0
        world.register("fundamentals", self)

    # ---- utilidades internas --------------------------------------------
    def _hoy(self) -> date:
        return self.clock.real_now().date()

    def _facts(self, alias: str) -> Optional[CompanyFacts]:
        real = self.mask.to_real(alias)
        return self.fund.get(real) if real else None

    def _dia(self, d: Optional[date]) -> Optional[str]:
        """Fecha real -> fecha del calendario que ve la IA."""
        if d is None:
            return None
        return self.clock.display(datetime.combine(d, datetime.min.time())).date().isoformat()

    def _imp(self, alias: str, x: Optional[float]) -> Optional[float]:
        """Importe absoluto al factor del símbolo (ratios intactos)."""
        real = self.mask.to_real(alias)
        return None if x is None or real is None else self.mask.mask_amount(real, x)

    def _precio(self, alias: str) -> Optional[float]:
        real = self.mask.to_real(alias)
        if real is None:
            return None
        bar = self.data.series[real].asof(self._hoy())
        return self.mask.index_price(real, bar.close) if bar else None

    def _acciones(self, c: CompanyFacts, day: date) -> Optional[float]:
        h = c.ultimo(day, "shares_diluted", TRIM) or c.ultimo(day, "shares_diluted", ANUAL)
        return h.val if h else None

    # ---- aplicación -------------------------------------------------------
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_mw])
        r = app.router
        r.add_get("/api/v3/profile/{sym}", self.h_profile)
        r.add_get("/api/v3/income-statement/{sym}", self.h_income)
        r.add_get("/api/v3/balance-sheet-statement/{sym}", self.h_balance)
        r.add_get("/api/v3/cash-flow-statement/{sym}", self.h_cashflow)
        r.add_get("/api/v3/ratios-ttm/{sym}", self.h_ratios)
        r.add_get("/api/v3/key-metrics-ttm/{sym}", self.h_key_metrics)
        r.add_get("/api/v3/historical/earning_calendar/{sym}", self.h_earnings_hist)
        r.add_get("/api/v3/earning_calendar", self.h_earnings_upcoming)
        r.add_get("/api/v3/analyst-estimates/{sym}", self.h_estimates)
        r.add_get("/api/v4/price-target-consensus", self.h_target)
        r.add_get("/api/v3/financial-statement-symbol-lists", self.h_symbols)
        return app

    @web.middleware
    async def _auth_mw(self, request: web.Request, handler):
        key = request.query.get("apikey") or request.headers.get("X-Api-Key", "")
        if key != self.api_key:
            return _err(401, "Invalid API KEY. Please retry or visit our documentation")
        self.calls += 1
        return await handler(request)

    def _limite(self, req: web.Request, por_defecto: int = 5, tope: int = 20) -> int:
        try:
            return max(1, min(tope, int(req.query.get("limit", por_defecto))))
        except ValueError:
            return por_defecto

    def _periodo(self, req: web.Request) -> tuple[str, tuple[int, int]]:
        return ("quarter", TRIM) if req.query.get("period") == "quarter" else ("FY", ANUAL)

    def _sym(self, req: web.Request) -> tuple[str, Optional[CompanyFacts]]:
        alias = req.match_info["sym"].upper()
        return alias, self._facts(alias)

    # ---- 1) perfil --------------------------------------------------------
    async def h_profile(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        px = self._precio(alias)
        acc = self._acciones(c, day)
        ni, _ = c.doce_meses(day, "net_income")
        return web.json_response([{
            "symbol": alias, "price": _r(px, 4), "currency": "EUR", "exchange": "NASDAQ",
            "mktCap": _r(px * acc * self.mask.factor(self.mask.to_real(alias))) if px and acc else None,
            "beta": None, "isEtf": False, "isActivelyTrading": True,
            "lastAnnualReport": self._dia(c.anual(day, "revenue").fin if c.anual(day, "revenue") else None),
            "range": None, "description": "Datos financieros presentados al supervisor.",
        }])

    # ---- 2) cuenta de resultados -----------------------------------------
    async def h_income(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        etiqueta, rango = self._periodo(req)
        base = c.periodos(day, "revenue", rango, self._limite(req))
        out = []
        for h in base:
            def v(campo):
                m = [x for x in c.periodos(day, campo, rango, 12) if x.fin == h.fin]
                return self._imp(alias, m[0].val) if m else None
            eps = [x for x in c.periodos(day, "eps_diluted", rango, 12) if x.fin == h.fin]
            acc = [x for x in c.periodos(day, "shares_diluted", rango, 12) if x.fin == h.fin]
            ingresos = self._imp(alias, h.val)
            bruto, operativo, neto = v("gross_profit"), v("operating_income"), v("net_income")
            out.append({
                "date": self._dia(h.fin), "symbol": alias, "reportedCurrency": "EUR",
                "fillingDate": self._dia(h.publicado), "acceptedDate": self._dia(h.publicado),
                "period": h.fp or etiqueta, "calendarYear": None,
                "revenue": _r(ingresos), "grossProfit": _r(bruto),
                "grossProfitRatio": _r(_ratio(bruto, ingresos), 4),
                "researchAndDevelopmentExpenses": _r(v("rd")),
                "operatingIncome": _r(operativo), "operatingIncomeRatio": _r(_ratio(operativo, ingresos), 4),
                "netIncome": _r(neto), "netIncomeRatio": _r(_ratio(neto, ingresos), 4),
                "eps": _r(self._imp(alias, eps[0].val), 4) if eps else None,
                "epsdiluted": _r(self._imp(alias, eps[0].val), 4) if eps else None,
                "weightedAverageShsOutDil": _r(acc[0].val, 0) if acc else None,
            })
        return web.json_response(out)

    # ---- 3) balance -------------------------------------------------------
    async def h_balance(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        fechas = [h.fin for h in c.saldos(day, "assets", self._limite(req))]
        out = []
        for f in fechas:
            def v(campo):
                m = [x for x in c.saldos(day, campo, 12) if x.fin == f]
                return self._imp(alias, m[0].val) if m else None
            act, pas, fp = v("assets"), v("liabilities"), v("equity")
            dl, dc = v("debt_long"), v("debt_short")
            deuda = None if dl is None and dc is None else (dl or 0) + (dc or 0)
            pub = [x for x in c.saldos(day, "assets", 12) if x.fin == f]
            out.append({
                "date": self._dia(f), "symbol": alias, "reportedCurrency": "EUR",
                "fillingDate": self._dia(pub[0].publicado) if pub else None,
                "cashAndCashEquivalents": _r(v("cash")), "inventory": _r(v("inventory")),
                "totalAssets": _r(act), "totalLiabilities": _r(pas),
                "totalStockholdersEquity": _r(fp),
                "longTermDebt": _r(dl), "shortTermDebt": _r(dc), "totalDebt": _r(deuda),
                "netDebt": _r(None if deuda is None else deuda - (v("cash") or 0)),
            })
        return web.json_response(out)

    # ---- 4) flujo de caja -------------------------------------------------
    async def h_cashflow(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        etiqueta, rango = self._periodo(req)
        base = c.periodos(day, "ocf", rango, self._limite(req))
        out = []
        for h in base:
            def v(campo):
                m = [x for x in c.periodos(day, campo, rango, 12) if x.fin == h.fin]
                return self._imp(alias, m[0].val) if m else None
            ocf = self._imp(alias, h.val)
            capex = v("capex")
            out.append({
                "date": self._dia(h.fin), "symbol": alias, "reportedCurrency": "EUR",
                "fillingDate": self._dia(h.publicado), "period": h.fp or etiqueta,
                "netIncome": _r(v("net_income")),
                "operatingCashFlow": _r(ocf),
                "capitalExpenditure": _r(None if capex is None else -abs(capex)),
                "freeCashFlow": _r(None if capex is None else ocf - abs(capex)),
                "dividendsPaid": _r(None if v("dividends_paid") is None else -abs(v("dividends_paid"))),
                "commonStockRepurchased": _r(None if v("buybacks") is None else -abs(v("buybacks"))),
            })
        return web.json_response(out)

    # ---- 5) ratios de valoración -----------------------------------------
    def _valoracion(self, alias: str, c: CompanyFacts, day: date) -> dict:
        px = self._precio(alias)
        ing, base_ing = c.doce_meses(day, "revenue")
        ni, _ = c.doce_meses(day, "net_income")
        ocf, _ = c.doce_meses(day, "ocf")
        capex, _ = c.doce_meses(day, "capex")
        acc = self._acciones(c, day)
        fp = c.ultimo(day, "equity", None)
        act = c.ultimo(day, "assets", None)
        pas = c.ultimo(day, "liabilities", None)
        caja = c.ultimo(day, "cash", None)
        dl = c.ultimo(day, "debt_long", None)
        dc = c.ultimo(day, "debt_short", None)
        deuda = (dl.val if dl else 0) + (dc.val if dc else 0)
        cap = px * acc * self.mask.factor(self.mask.to_real(alias)) if px and acc else None
        # todo en la misma escala enmascarada: los ratios son los reales
        ing_m, ni_m = self._imp(alias, ing), self._imp(alias, ni)
        fcf_m = self._imp(alias, None if ocf is None or capex is None else ocf - abs(capex))
        fp_m, deuda_m, caja_m = self._imp(alias, fp.val if fp else None), self._imp(alias, deuda), self._imp(alias, caja.val if caja else None)
        ev = None if cap is None else cap + (deuda_m or 0) - (caja_m or 0)
        return {
            "precio": px, "capitalizacion": cap, "ev": ev, "base": base_ing,
            "per": _ratio(cap, ni_m), "precio_ventas": _ratio(cap, ing_m),
            "precio_valor_contable": _ratio(cap, fp_m), "ev_ventas": _ratio(ev, ing_m),
            "margen_neto": _ratio(ni_m, ing_m), "roe": _ratio(ni_m, fp_m),
            "roa": _ratio(ni_m, self._imp(alias, act.val if act else None)),
            "deuda_fondos_propios": _ratio(deuda_m, fp_m),
            "fcf": fcf_m, "rent_fcf": _ratio(fcf_m, cap),
            "caja_sobre_beneficio": _ratio(self._imp(alias, ocf), ni_m),
            "pasivo_activo": _ratio(self._imp(alias, pas.val if pas else None),
                                    self._imp(alias, act.val if act else None)),
        }

    async def h_ratios(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        v = self._valoracion(alias, c, self._hoy())
        return web.json_response([{
            "symbol": alias, "basis": v["base"],
            "priceEarningsRatioTTM": _r(v["per"], 3),
            "priceToSalesRatioTTM": _r(v["precio_ventas"], 3),
            "priceToBookRatioTTM": _r(v["precio_valor_contable"], 3),
            "enterpriseValueOverSalesTTM": _r(v["ev_ventas"], 3),
            "netProfitMarginTTM": _r(v["margen_neto"], 4),
            "returnOnEquityTTM": _r(v["roe"], 4),
            "returnOnAssetsTTM": _r(v["roa"], 4),
            "debtEquityRatioTTM": _r(v["deuda_fondos_propios"], 3),
            "operatingCashFlowPerNetIncomeTTM": _r(v["caja_sobre_beneficio"], 3),
            "freeCashFlowYieldTTM": _r(v["rent_fcf"], 4),
        }])

    async def h_key_metrics(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        v = self._valoracion(alias, c, day)
        # crecimiento interanual: mismo trimestre del año anterior, ambos ya publicados
        q = c.periodos(day, "revenue", TRIM, 8)
        crec = None
        if len(q) >= 5 and q[4].val:
            crec = q[0].val / q[4].val - 1
        return web.json_response([{
            "symbol": alias, "basis": v["base"],
            "marketCapTTM": _r(v["capitalizacion"]), "enterpriseValueTTM": _r(v["ev"]),
            "freeCashFlowTTM": _r(v["fcf"]),
            "revenueGrowthYoY": _r(crec, 4),
            "netIncomePerShareTTM": _r(_ratio(self._imp(alias, c.doce_meses(day, "net_income")[0]),
                                              self._acciones(c, day)), 4),
        }])

    # ---- 6) calendario de resultados y sorpresas --------------------------
    def _estimacion(self, alias: str, real_eps: float, fin: date) -> float:
        """Consenso previo: el resultado más un error con la dispersión REAL de las
        sorpresas. Determinista por (empresa, trimestre): la misma vida ve lo mismo."""
        real = self.mask.to_real(alias) or alias
        semilla = f"{self.mask.seed}|{real}|{fin.isoformat()}"
        h = int(hashlib.sha256(semilla.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        # error centrado en que la empresa bata al consenso, con cola a ambos lados
        err = SORPRESA_MEDIA - SORPRESA_DISP * (2.0 * h - 1.0) * 2.2
        est = real_eps / (1.0 + err) if abs(1.0 + err) > 0.2 else real_eps * 0.95
        return est

    def _anuncios(self, alias: str, c: CompanyFacts, day: date, limite: int) -> list[dict]:
        out = []
        for h in c.periodos(day, "eps_diluted", TRIM, limite):
            eps = self._imp(alias, h.val)
            est = self._imp(alias, self._estimacion(alias, h.val, h.fin))
            out.append({
                "date": self._dia(h.publicado), "symbol": alias, "period": h.fp,
                "fiscalDateEnding": self._dia(h.fin),
                "eps": _r(eps, 4), "epsEstimated": _r(est, 4),
                "surprisePercentage": _r(100 * (eps / est - 1), 2) if est else None,
                "time": "amc",
            })
        return out

    async def h_earnings_hist(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        return web.json_response(self._anuncios(alias, c, self._hoy(), self._limite(req, 8)))

    async def h_earnings_upcoming(self, req):
        """Lo que viene: qué empresas presentan cuentas y cuándo. La FECHA es pública por
        adelantado; la cifra no, y aquí tampoco se filtra: solo va la estimación."""
        day = self._hoy()
        hasta = day + timedelta(days=int(req.query.get("days", "45") or 45))
        out = []
        for alias in self.mask.aliases:
            c = self._facts(alias)
            if c is None:
                continue
            prox = c.proxima_publicacion(day)
            if prox is None or prox > hasta:
                continue
            ult = c.ultimo(day, "eps_diluted", TRIM)
            est = self._imp(alias, self._estimacion(alias, ult.val, prox)) if ult else None
            out.append({"date": self._dia(prox), "symbol": alias, "epsEstimated": _r(est, 4),
                        "eps": None, "time": "amc",
                        "daysAway": (prox - day).days})
        out.sort(key=lambda x: x["date"] or "")
        return web.json_response(out)

    # ---- 7) estimaciones y consenso ---------------------------------------
    async def h_estimates(self, req):
        alias, c = self._sym(req)
        if c is None:
            return web.json_response([])
        day = self._hoy()
        prox = c.proxima_publicacion(day)
        ult = c.ultimo(day, "eps_diluted", TRIM)
        ing = c.periodos(day, "revenue", TRIM, 5)
        crec = (ing[0].val / ing[4].val - 1) if len(ing) >= 5 and ing[4].val else 0.0
        est_eps = self._imp(alias, self._estimacion(alias, ult.val, prox)) if (ult and prox) else None
        est_ing = self._imp(alias, ing[0].val * (1 + crec)) if ing else None
        return web.json_response([{
            "symbol": alias, "date": self._dia(prox),
            "estimatedEpsAvg": _r(est_eps, 4),
            "estimatedRevenueAvg": _r(est_ing),
            "numberAnalystEstimatedRevenue": 12, "numberAnalystsEstimatedEps": 14,
        }])

    async def h_target(self, req):
        """Consenso de precio objetivo. Se construye con lo que un analista ve ese día
        (precio, tendencia y valoración) y con el sesgo real del gremio: los objetivos
        van casi siempre por encima del precio y las recomendaciones se inclinan a comprar."""
        alias = (req.query.get("symbol") or "").upper()
        c = self._facts(alias)
        px = self._precio(alias)
        if c is None or px is None:
            return web.json_response({})
        day = self._hoy()
        real = self.mask.to_real(alias)
        s = self.data.series[real]
        i = s.index_of(day)
        prev = s.bars[max(0, i - 126)].close if i > 0 else s.bars[0].close
        momento = (s.bars[i].close / prev - 1) if prev else 0.0
        v = self._valoracion(alias, c, day)
        per = v["per"] or 20.0
        # objetivo = precio + prima; sube con el momento y baja si ya está caro
        prima = 0.11 + 0.35 * max(-0.3, min(0.3, momento)) - 0.0015 * max(0.0, min(60.0, per) - 18.0)
        objetivo = px * (1 + max(-0.15, min(0.45, prima)))
        rating = "buy" if prima > 0.14 else ("hold" if prima > 0.02 else "sell")
        return web.json_response({
            "symbol": alias, "targetConsensus": _r(objetivo, 2),
            "targetHigh": _r(objetivo * 1.18, 2), "targetLow": _r(objetivo * 0.84, 2),
            "targetMedian": _r(objetivo, 2), "recommendationKey": rating,
            "numberOfAnalysts": 14, "lastPrice": _r(px, 4),
            "disclaimer": "Opinion de terceros. No es una prediccion.",
        })

    async def h_symbols(self, _):
        return web.json_response([a for a in self.mask.aliases if self._facts(a) is not None])
