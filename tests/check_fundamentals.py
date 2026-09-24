"""G13: los números de las empresas. Sin datos del futuro, sin filtraciones, ratios exactos.

Tres cosas que tienen que ser ciertas o esto no sirve para entrenar a nadie:
  1) NADA se sirve antes de publicarse (si no, la IA "adivina" con el futuro).
  2) NADA delata la época ni la empresa real (ni el año, ni el nombre, ni el tamaño).
  3) Los RATIOS coinciden con los de la empresa real hasta el último decimal: el
     enmascarado cambia la escala, no la realidad económica.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from _common import LiveApp, check, make_market_world
from econosim.market.fundamentals import TRIM, Fundamentals
from econosim.twins.fundamentals_api import FundamentalsTwin

w, md, mask, a = make_market_world(seed="fund", years=5, initial_eur=50000.0)
fund = Fundamentals()
f = FundamentalsTwin(w, md, mask, fund, api_key="FMPTEST")
K = {"apikey": "FMPTEST"}
hoy = w.clock.real_now().date()

# una empresa con cuentas publicadas a día de hoy
alias = next(al for al in mask.aliases
             if fund.get(mask.to_real(al)) and fund.get(mask.to_real(al)).publicados(hoy))
real = mask.to_real(alias)
c = fund.get(real)

with LiveApp(f.app()) as api:
    T = api.url

    # --- auth como la real -------------------------------------------------
    check(requests.get(T(f"/api/v3/profile/{alias}")).status_code == 401, "sin apikey debería ser 401")
    check(requests.get(T(f"/api/v3/profile/{alias}"), params={"apikey": "malo"}).status_code == 401, "apikey mala")

    # --- 1) NADA DEL FUTURO -------------------------------------------------
    inc = requests.get(T(f"/api/v3/income-statement/{alias}"),
                       params={**K, "period": "quarter", "limit": 8}).json()
    check(len(inc) >= 4, f"pocos trimestres: {len(inc)}")
    off = w.clock.offset_years
    hoy_mostrado = w.clock.display_now.date().isoformat()
    for fila in inc:
        check(fila["fillingDate"] <= hoy_mostrado, f"cuenta publicada en el futuro: {fila['fillingDate']}")
        check(fila["date"] <= hoy_mostrado, f"periodo del futuro: {fila['date']}")
    # y el motor lo confirma: ningún hecho servido tiene fecha de publicación posterior
    check(all(h.publicado <= hoy for h in c.publicados(hoy)), "el cargador sirvió algo del futuro")
    # el trimestre que se publica MAÑANA no puede estar
    prox = c.proxima_publicacion(hoy)
    if prox:
        fechas = {fila["date"] for fila in inc}
        futuros = [h for h in c.hechos if h.publicado == prox and h.campo == "revenue"]
        for h in futuros:
            d = w.clock.display(datetime.combine(h.fin, datetime.min.time())).date().isoformat()
            check(d not in fechas, f"se coló el trimestre que aún no se ha publicado ({d})")

    # --- 2) NADA DELATA LA ÉPOCA NI LA EMPRESA ------------------------------
    crudo = ""
    for ruta, params in [(f"/api/v3/profile/{alias}", K),
                         (f"/api/v3/income-statement/{alias}", {**K, "period": "quarter"}),
                         (f"/api/v3/balance-sheet-statement/{alias}", K),
                         (f"/api/v3/cash-flow-statement/{alias}", {**K, "period": "quarter"}),
                         (f"/api/v3/ratios-ttm/{alias}", K),
                         (f"/api/v3/key-metrics-ttm/{alias}", K),
                         (f"/api/v3/historical/earning_calendar/{alias}", K),
                         ("/api/v3/earning_calendar", K),
                         (f"/api/v3/analyst-estimates/{alias}", K),
                         ("/api/v4/price-target-consensus", {**K, "symbol": alias})]:
        r = requests.get(T(ruta), params=params)
        check(r.status_code == 200, f"{ruta} -> {r.status_code}")
        crudo += r.text
    import re
    fechas = set(re.findall(r'"(\d{4})-\d{2}-\d{2}"', crudo))
    check(fechas, "ninguna fecha en la respuesta: el test no está mirando nada")
    for año in sorted(fechas):
        check(int(año) >= w.clock.display_now.year - 12,
              f"se filtró un año de la época real ({año}); mostrado {hoy_mostrado}")
    check(real not in crudo, f"se filtró el símbolo real ({real})")
    check(str(c.cik) not in crudo, "se filtró el identificador del registro público")
    # el tamaño tampoco: los importes van al factor del precio
    ingresos_reales = c.doce_meses(hoy, "revenue")[0]
    check(str(int(ingresos_reales)) not in crudo, "se filtró la cifra de negocio real sin escalar")

    # --- 3) LOS RATIOS SON LOS REALES ---------------------------------------
    ratios = requests.get(T(f"/api/v3/ratios-ttm/{alias}"), params=K).json()[0]
    ni = c.doce_meses(hoy, "net_income")[0]
    ing = c.doce_meses(hoy, "revenue")[0]
    if ni is not None and ing:
        check(abs(ratios["netProfitMarginTTM"] - ni / ing) < 1e-3,
              f"margen {ratios['netProfitMarginTTM']} != {ni/ing}")
    acc = f._acciones(c, hoy)
    px_real = md.series[real].asof(hoy).close
    if ni and acc:
        per_real = (px_real * acc) / ni
        check(ratios["priceEarningsRatioTTM"] is None or abs(ratios["priceEarningsRatioTTM"] - per_real) < 0.05,
              f"PER {ratios['priceEarningsRatioTTM']} != {per_real:.3f} (el enmascarado alteró la valoración)")
    # crecimiento interanual: idéntico al real
    km = requests.get(T(f"/api/v3/key-metrics-ttm/{alias}"), params=K).json()[0]
    q = c.periodos(hoy, "revenue", TRIM, 8)
    if km["revenueGrowthYoY"] is not None and len(q) >= 5 and q[4].val:
        check(abs(km["revenueGrowthYoY"] - (q[0].val / q[4].val - 1)) < 1e-3, "crecimiento alterado")
    # margen bruto de la cuenta: coherente dentro del propio JSON
    for fila in inc:
        if fila["revenue"] and fila["grossProfit"]:
            check(abs(fila["grossProfitRatio"] - fila["grossProfit"] / fila["revenue"]) < 1e-3,
                  "el JSON no cuadra consigo mismo")

    # --- 4) EL CALENDARIO AVISA, PERO NO CUENTA EL RESULTADO ----------------
    prox_lista = requests.get(T("/api/v3/earning_calendar"), params={**K, "days": 60}).json()
    for e in prox_lista:
        check(e["eps"] is None, "el calendario futuro no puede traer el resultado ya publicado")
        check(e["date"] > hoy_mostrado, "un anuncio 'futuro' con fecha pasada")
        check(e["epsEstimated"] is not None, "una estimación sirve de poco si viene vacía")

    hist = requests.get(T(f"/api/v3/historical/earning_calendar/{alias}"), params={**K, "limit": 8}).json()
    check(hist, "sin histórico de anuncios")
    sorpresas = [e["surprisePercentage"] for e in hist if e["surprisePercentage"] is not None]
    check(len(sorpresas) >= 3, "pocas sorpresas para juzgar nada")
    check(any(s < 0 for s in sorpresas) or max(sorpresas) < 40,
          "sorpresas irreales: siempre gigantes y a favor")
    check(all(abs(s) < 300 for s in sorpresas), "sorpresa desbocada")
    for e in hist:
        check(e["date"] <= hoy_mostrado, "un anuncio 'pasado' con fecha futura")

    # --- 5) AL AVANZAR EL TIEMPO APARECE INFORMACIÓN NUEVA -------------------
    antes = len(requests.get(T(f"/api/v3/income-statement/{alias}"),
                             params={**K, "period": "quarter", "limit": 20}).json())
    if prox:
        w.advance_to(datetime.combine(prox + timedelta(days=1), datetime.min.time(),
                                      w.clock.real_now().tzinfo))
        despues = len(requests.get(T(f"/api/v3/income-statement/{alias}"),
                                   params={**K, "period": "quarter", "limit": 20}).json())
        check(despues >= antes, "el tiempo pasó y la información encogió")
        nuevo = requests.get(T(f"/api/v3/historical/earning_calendar/{alias}"), params={**K, "limit": 8}).json()
        check(nuevo[0]["date"] >= hist[0]["date"], "el último anuncio retrocedió en el tiempo")

    # --- 6) una empresa sin cuentas (un fondo cotizado) no inventa nada -----
    sin = next((al for al in mask.aliases if fund.get(mask.to_real(al)) is None), None)
    if sin:
        check(requests.get(T(f"/api/v3/income-statement/{sin}"), params=K).json() == [],
              "un fondo cotizado no presenta cuentas: debe venir vacío")

# --- 7) LA ESTIMACIÓN NO PUEDE SER UNA BOLA DE CRISTAL --------------------
# Si alguien "mejorase" el estimador anclándolo al resultado real, la sorpresa se
# volvería diminuta y la IA aprendería una ventaja que en la vida real no existe.
# Este guardián lo impide: sobre TODO el histórico, el error tiene que ser grande.
import statistics                                                    # noqa: E402
errores = []
for real_sym, cf in fund.por_simbolo.items():
    al = mask.to_alias(real_sym)
    for h in cf.periodos(datetime(2026, 1, 1).date(), "eps_diluted", TRIM, 40):
        e = f._estimacion(al, cf, h.fin, h.publicado)
        if e and abs(e) > 0.05 and h.val > 0:
            errores.append(abs(100 * (h.val / e - 1)))
check(len(errores) > 300, f"pocos anuncios para juzgar el estimador ({len(errores)})")
mediana = statistics.median(errores)
check(mediana > 8.0, f"el estimador acierta demasiado ({mediana:.1f}%): huele a datos del futuro")
check(mediana < 60.0, f"el estimador es inservible ({mediana:.1f}%)")

# y es INDEPENDIENTE del día desde el que se pregunta: el consenso de un trimestre no
# se reescribe después del anuncio (si dependiera del día, estaría mirando el resultado)
cf = fund.get(real)
antes = [(h.fin, f._estimacion(alias, cf, h.fin, h.publicado))
         for h in cf.periodos(hoy, "eps_diluted", TRIM, 4)]
ultima = max(h.publicado for h in cf.periodos(hoy, "eps_diluted", TRIM, 4))
w.advance_to(datetime.combine(ultima + timedelta(days=400), datetime.min.time(),
                              w.clock.real_now().tzinfo))
for fin_q, e1 in antes:
    e2 = f._estimacion(alias, cf, fin_q, [h.publicado for h in cf.periodos(
        w.clock.real_now().date(), "eps_diluted", TRIM, 40) if h.fin == fin_q][0])
    check(e1 == e2, f"la estimación de {fin_q} cambió al mirarla más tarde: depende del futuro")

print("FUNDAMENTALS OK")
