"""Descarga los fundamentales REALES y con FECHA DE PUBLICACIÓN (SEC EDGAR companyfacts).

Cada dato lleva su `filed`: el día en que la empresa lo hizo público. El simulador solo
sirve los hechos con filed <= día virtual, así que la IA nunca ve una cifra antes de que
existiera (sin sesgo de anticipación). Esto es lo que hace que "invertir mirando los
números" se pueda entrenar de verdad.

    python scripts/fetch_fundamentals.py            # todos los tickers de data/market
    python scripts/fetch_fundamentals.py CAT AAPL   # solo algunos

Salida: data/fundamentals/<TICKER>.json  +  manifest.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKET = ROOT / "data" / "market"
OUT = ROOT / "data" / "fundamentals"
SINCE = "2003-01-01"          # margen antes del primer día de precios (2004)

# La SEC exige identificarse; sin esto devuelve 403.
# La SEC exige un contacto en el User-Agent: ponlo en .env (SEC_USER_AGENT), no en el código.
UA = __import__("os").environ.get("SEC_USER_AGENT", "econosim research (define SEC_USER_AGENT con tu correo)")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Los conceptos que de verdad se miran, con sus sinónimos XBRL (las empresas no usan
# todas la misma etiqueta). El primero que exista gana.
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"],
    "inventory": ["InventoryNet"],
    "debt_long": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_short": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "rd": ["ResearchAndDevelopmentExpense"],
}
# Un dato de flujo (ingresos, beneficio) cubre un periodo; uno de saldo (activo, caja) es
# una foto a una fecha. Se guardan igual, pero el periodo se marca con start/end.
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return json.loads(raw)


def cik_map() -> dict[str, int]:
    data = get(TICKERS_URL)
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def extract(facts: dict) -> tuple[list[dict], dict[str, str]]:
    """Aplana companyfacts a una lista de hechos con su fecha de publicación."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out: list[dict] = []
    usados: dict[str, str] = {}
    for campo, etiquetas in CONCEPTS.items():
        for tag in etiquetas:
            nodo = gaap.get(tag)
            if not nodo:
                continue
            unidades = nodo.get("units", {})
            unidad = next((u for u in ("USD", "USD/shares", "shares", "pure") if u in unidades), None)
            if unidad is None:
                continue
            n = 0
            for f in unidades[unidad]:
                if f.get("form") not in FORMS or not f.get("filed") or f["filed"] < SINCE:
                    continue
                out.append({"campo": campo, "unidad": unidad, "val": f.get("val"),
                            "inicio": f.get("start"), "fin": f.get("end"), "publicado": f["filed"],
                            "forma": f["form"], "fy": f.get("fy"), "fp": f.get("fp"),
                            "frame": f.get("frame")})
                n += 1
            if n:
                usados[campo] = tag
                break        # la primera etiqueta que existe manda; no se mezclan sinónimos
    # el mismo hecho aparece repetido en informes posteriores: nos quedamos con la
    # PRIMERA publicación de cada (campo, periodo), que es cuando el mercado lo supo
    primero: dict[tuple, dict] = {}
    for f in out:
        k = (f["campo"], f["inicio"], f["fin"])
        if k not in primero or f["publicado"] < primero[k]["publicado"]:
            primero[k] = f
    hechos = sorted(primero.values(), key=lambda f: (f["publicado"], f["campo"], f["fin"] or ""))
    return hechos, usados


def main() -> None:
    pedidos = [t.upper() for t in sys.argv[1:]]
    tickers = pedidos or sorted(p.stem for p in MARKET.glob("*.csv"))
    OUT.mkdir(parents=True, exist_ok=True)
    ciks = cik_map()
    manifest = {}
    if (OUT / "manifest.json").exists():
        manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    manifest.setdefault("_fuente", {
        "proveedor": "SEC EDGAR XBRL companyfacts",
        "clave": "cada hecho lleva 'publicado' (filed): el día en que se hizo público",
        "nota": "los ETF no presentan cuentas; se omiten",
    })
    manifest["_fuente"]["descargado"] = time.strftime("%Y-%m-%d")
    manifest.setdefault("simbolos", {})

    for t in tickers:
        cik = ciks.get(t)
        if cik is None:
            print(f"  {t}: sin CIK (¿es un ETF?), se omite")
            continue
        try:
            facts = get(FACTS_URL.format(cik=cik))
        except Exception as e:
            print(f"  {t}: fallo {type(e).__name__} {e}")
            continue
        hechos, usados = extract(facts)
        if not hechos:
            print(f"  {t}: sin hechos utilizables")
            continue
        (OUT / f"{t}.json").write_text(json.dumps(
            {"cik": cik, "etiquetas": usados, "hechos": hechos}, indent=0), encoding="utf-8")
        manifest["simbolos"][t] = {"cik": cik, "hechos": len(hechos),
                                   "primero": hechos[0]["publicado"], "ultimo": hechos[-1]["publicado"],
                                   "campos": sorted(usados)}
        print(f"  {t}: {len(hechos)} hechos  {hechos[0]['publicado']} -> {hechos[-1]['publicado']}"
              f"  ({len(usados)} campos)", flush=True)
        time.sleep(0.2)          # la SEC pide menos de 10 peticiones por segundo

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(manifest['simbolos'])} empresas en {OUT}")


if __name__ == "__main__":
    main()
