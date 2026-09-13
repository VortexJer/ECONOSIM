"""Lo que ve la IA cuando estudia una empresa. Recorre el gemelo igual que un inversor.

    python scripts/demo_fundamentales.py [semilla]

Sirve para juzgar de un vistazo si esto se parece a la realidad: mismos apartados que
scripts/study_investor_toolkit.py (el recorrido con datos reales), pero pasando por el
gemelo, con la fecha virtual y todo enmascarado.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _common import LiveApp, make_market_world           # noqa: E402
from econosim.market.fundamentals import Fundamentals    # noqa: E402
from econosim.twins.fundamentals_api import FundamentalsTwin  # noqa: E402

SEMILLA = sys.argv[1] if len(sys.argv) > 1 else "demo"
w, md, mask, alp = make_market_world(seed=SEMILLA, years=5, initial_eur=50000.0)
fund = Fundamentals()
tw = FundamentalsTwin(w, md, mask, fund, api_key="K")
K = {"apikey": "K"}
hoy = w.clock.real_now().date()
alias = next(a for a in mask.aliases if fund.get(mask.to_real(a)) and fund.get(mask.to_real(a)).publicados(hoy))


def sec(t: str) -> None:
    print(f"\n{'='*74}\n{t}\n{'='*74}")


def money(x) -> str:
    if x is None:
        return "—"
    x = float(x)
    for u, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= d:
            return f"{x/d:,.2f}{u}"
    return f"{x:,.2f}"


with LiveApp(tw.app()) as api:
    def g(ruta, **p):
        return requests.get(api.url(ruta), params={**K, **p}).json()

    print(f"HOY (calendario de la IA): {w.clock.display_now.date()}   ·   empresa: {alias}")

    sec("1) CUENTA DE RESULTADOS  ·  /api/v3/income-statement?period=quarter")
    inc = g(f"/api/v3/income-statement/{alias}", period="quarter", limit=5)
    print(f"{'cierre':<12}{'publicado':<12}{'ingresos':>12}{'margen bruto':>14}{'beneficio':>12}{'margen':>9}")
    for f in inc:
        mb = f"{100*f['grossProfitRatio']:.1f}%" if f.get("grossProfitRatio") else "—"
        mn = f"{100*f['netIncomeRatio']:.1f}%" if f.get("netIncomeRatio") else "—"
        print(f"{f['date']:<12}{f['fillingDate']:<12}{money(f['revenue']):>12}{mb:>14}"
              f"{money(f['netIncome']):>12}{mn:>9}")
    if len(inc) >= 5 and inc[0]["revenue"] and inc[4]["revenue"]:
        print(f"  -> crecimiento interanual: {100*(inc[0]['revenue']/inc[4]['revenue']-1):+.1f}%")

    sec("2) BALANCE  ·  /api/v3/balance-sheet-statement")
    bs = g(f"/api/v3/balance-sheet-statement/{alias}", limit=1)
    if bs:
        b = bs[0]
        for k, e in [("totalAssets", "activo"), ("totalLiabilities", "pasivo"),
                     ("totalStockholdersEquity", "fondos propios"), ("cashAndCashEquivalents", "caja"),
                     ("totalDebt", "deuda"), ("netDebt", "deuda neta")]:
            print(f"  {e:<20} {money(b.get(k)):>14}")

    sec("3) FLUJO DE CAJA  ·  /api/v3/cash-flow-statement?period=quarter")
    cf = g(f"/api/v3/cash-flow-statement/{alias}", period="quarter", limit=1)
    if cf:
        c = cf[0]
        for k, e in [("operatingCashFlow", "caja de la operación"), ("capitalExpenditure", "inversión"),
                     ("freeCashFlow", "caja libre"), ("dividendsPaid", "dividendos"),
                     ("commonStockRepurchased", "recompras")]:
            print(f"  {e:<24} {money(c.get(k)):>14}")

    sec("4) VALORACIÓN  ·  /api/v3/ratios-ttm  +  /api/v3/key-metrics-ttm")
    r = (g(f"/api/v3/ratios-ttm/{alias}") or [{}])[0]
    km = (g(f"/api/v3/key-metrics-ttm/{alias}") or [{}])[0]
    print(f"  base de cálculo:      {r.get('basis')}")
    for k, e in [("priceEarningsRatioTTM", "PER"), ("priceToSalesRatioTTM", "precio/ventas"),
                 ("priceToBookRatioTTM", "precio/valor contable"), ("netProfitMarginTTM", "margen neto"),
                 ("returnOnEquityTTM", "ROE"), ("debtEquityRatioTTM", "deuda/fondos propios"),
                 ("operatingCashFlowPerNetIncomeTTM", "caja/beneficio"),
                 ("freeCashFlowYieldTTM", "rentabilidad de la caja libre")]:
        print(f"  {e:<32} {r.get(k)}")
    print(f"  {'capitalización':<32} {money(km.get('marketCapTTM'))}")
    print(f"  {'crecimiento interanual':<32} {km.get('revenueGrowthYoY')}")

    sec("5) CALENDARIO Y SORPRESAS  ·  /api/v3/historical/earning_calendar  +  /api/v3/earning_calendar")
    for e in g(f"/api/v3/historical/earning_calendar/{alias}", limit=5):
        print(f"  {e['date']}  {str(e['period']):<4} estimado={e['epsEstimated']}"
              f"  real={e['eps']}  sorpresa={e['surprisePercentage']}%")
    prox = g("/api/v3/earning_calendar", days=45)
    print(f"\n  próximas presentaciones de cuentas ({len(prox)} en 45 días):")
    for e in prox[:6]:
        print(f"    {e['date']}  {e['symbol']:<12} dentro de {e['daysAway']:>2} días  estimado={e['epsEstimated']}")

    sec("6) CONSENSO  ·  /api/v3/analyst-estimates  +  /api/v4/price-target-consensus")
    est = (g(f"/api/v3/analyst-estimates/{alias}") or [{}])[0]
    print(f"  próximo trimestre {est.get('date')}: beneficio por acción estimado {est.get('estimatedEpsAvg')}"
          f"  ingresos estimados {money(est.get('estimatedRevenueAvg'))}")
    t = g("/api/v4/price-target-consensus", symbol=alias)
    if t:
        subida = 100 * (t["targetConsensus"] / t["lastPrice"] - 1)
        print(f"  precio {t['lastPrice']}  objetivo {t['targetConsensus']} ({subida:+.1f}%)"
              f"  recomendación: {t['recommendationKey']}  ({t['numberOfAnalysts']} casas)")
