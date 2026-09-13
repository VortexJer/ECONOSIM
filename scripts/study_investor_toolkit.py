"""¿Qué mira DE VERDAD un inversor antes de entrar en una empresa, y qué le devuelve cada herramienta?

Recorre el caso real sobre una empresa cotizada con las fuentes que se usan en la vida real,
para luego replicarlas como gemelos en el sim. Imprime EXACTAMENTE lo que devuelve cada una.

    python scripts/study_investor_toolkit.py CAT
"""
from __future__ import annotations

import sys

import yfinance as yf

T = (sys.argv[1] if len(sys.argv) > 1 else "CAT").upper()
tk = yf.Ticker(T)


def sec(title: str) -> None:
    print(f"\n{'='*78}\n{title}\n{'='*78}")


def money(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    for u, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= d:
            return f"{x/d:,.2f}{u}"
    return f"{x:,.0f}"


# ---------------------------------------------------------------- 1) la cuenta de resultados
sec("1) CUENTA DE RESULTADOS (lo que gana) · fuente real: informes 10-K/10-Q (SEC EDGAR)")
inc = tk.income_stmt
if inc is not None and not inc.empty:
    filas = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "Diluted EPS"]
    cols = list(inc.columns)[:4]
    print("            " + "".join(f"{str(c.date()):>14}" for c in cols))
    for f in filas:
        if f in inc.index:
            print(f"{f:<24}" + "".join(f"{money(inc.loc[f, c]):>14}" for c in cols))
    # lo que de verdad se mira: crecimiento y margen
    if "Total Revenue" in inc.index and "Net Income" in inc.index and len(cols) >= 2:
        r0, r1 = float(inc.loc["Total Revenue", cols[0]]), float(inc.loc["Total Revenue", cols[1]])
        n0 = float(inc.loc["Net Income", cols[0]])
        print(f"\n  -> crecimiento de ingresos: {100*(r0/r1-1):+.1f}%   margen neto: {100*n0/r0:.1f}%")

# ---------------------------------------------------------------- 2) el balance
sec("2) BALANCE (si aguanta) · fuente real: mismo informe")
bs = tk.balance_sheet
if bs is not None and not bs.empty:
    c = bs.columns[0]
    for f in ["Total Debt", "Cash And Cash Equivalents", "Stockholders Equity", "Total Assets"]:
        if f in bs.index:
            print(f"  {f:<32} {money(bs.loc[f, c]):>14}")
    if "Total Debt" in bs.index and "Stockholders Equity" in bs.index:
        d, e = float(bs.loc["Total Debt", c]), float(bs.loc["Stockholders Equity", c])
        print(f"\n  -> deuda/fondos propios: {d/e:.2f}  ({'alta' if d/e > 2 else 'razonable'})")

# ---------------------------------------------------------------- 3) la caja
sec("3) FLUJO DE CAJA (si el beneficio es real) · fuente real: mismo informe")
cf = tk.cashflow
if cf is not None and not cf.empty:
    c = cf.columns[0]
    ocf = fcf = None
    for f in ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]:
        if f in cf.index:
            v = float(cf.loc[f, c])
            print(f"  {f:<32} {money(v):>14}")
            if f == "Operating Cash Flow":
                ocf = v
            if f == "Free Cash Flow":
                fcf = v
    if ocf and inc is not None and "Net Income" in inc.index:
        ni = float(inc.loc["Net Income", inc.columns[0]])
        print(f"\n  -> caja operativa / beneficio: {ocf/ni:.2f}  (>1 = el beneficio se convierte en caja)")

# ---------------------------------------------------------------- 4) valoración
sec("4) VALORACIÓN (si está cara) · fuente real: precio + los números de arriba")
info = {}
try:
    info = tk.info or {}
except Exception:
    pass
for k, etiqueta in [("trailingPE", "PER (12m)"), ("forwardPE", "PER estimado"),
                    ("priceToBook", "Precio/Valor contable"), ("enterpriseToEbitda", "EV/EBITDA"),
                    ("dividendYield", "Rentabilidad por dividendo %"), ("marketCap", "Capitalización"),
                    ("returnOnEquity", "ROE"), ("profitMargins", "Margen neto")]:
    v = info.get(k)
    if v is not None:
        print(f"  {etiqueta:<32} {money(v) if k == 'marketCap' else round(float(v), 3)}")

# ---------------------------------------------------------------- 5) el calendario
sec("5) CALENDARIO (cuándo hay volatilidad) · fuente real: calendario de resultados")
try:
    cal = tk.calendar
    if isinstance(cal, dict):
        for k in ("Earnings Date", "Ex-Dividend Date", "Dividend Date"):
            if k in cal:
                print(f"  {k:<24} {cal[k]}")
    ed = tk.get_earnings_dates(limit=6)
    if ed is not None and not ed.empty:
        print("\n  últimos anuncios (estimado vs real vs sorpresa):")
        for ts, row in ed.head(6).iterrows():
            print(f"    {str(ts.date()):<12} est={row.get('EPS Estimate')}  real={row.get('Reported EPS')}"
                  f"  sorpresa={row.get('Surprise(%)')}%")
except Exception as e:
    print("  (sin calendario)", type(e).__name__)

# ---------------------------------------------------------------- 6) qué opinan los analistas
sec("6) CONSENSO DE ANALISTAS (qué espera el mercado) · fuente real: consenso de casas")
try:
    rec = tk.recommendations
    if rec is not None and not rec.empty:
        print(rec.head(3).to_string())
except Exception:
    print("  (sin recomendaciones)")
for k, e in [("targetMeanPrice", "Precio objetivo medio"), ("recommendationKey", "Recomendación"),
             ("numberOfAnalystOpinions", "Nº de analistas"), ("currentPrice", "Precio actual")]:
    if info.get(k) is not None:
        print(f"  {e:<32} {info[k]}")

# ---------------------------------------------------------------- 7) quién compra y vende dentro
sec("7) INSIDERS E INSTITUCIONALES (qué hace quien sabe) · fuente real: formularios 4 / 13F")
try:
    ins = tk.insider_transactions
    if ins is not None and not ins.empty:
        print(ins.head(5)[[c for c in ["Start Date", "Insider", "Position", "Transaction", "Shares", "Value"]
                           if c in ins.columns]].to_string(index=False))
except Exception:
    print("  (sin operaciones de insiders)")
for k, e in [("heldPercentInstitutions", "% en manos de institucionales"),
             ("heldPercentInsiders", "% en manos de directivos")]:
    if info.get(k) is not None:
        print(f"  {e:<32} {round(float(info[k]) * 100, 1)}%")
