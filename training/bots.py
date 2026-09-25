"""Bots de REGLAS FIJAS (sin IA, sin aprendizaje) sobre los mismos datos de bolsa.

Como no aprenden nada, no pueden memorizar: TODOS los bloques de 6 meses de 2005 a 2026
son para ellos prueba limpia. Cada bot es una estrategia publicada, sin retocar parámetros
(retocarlos mirando los resultados sería volver a sobreajustar):

  tendencia_indice   Faber (2007), "A Quantitative Approach to Tactical Asset Allocation":
                     S&P 500 si está sobre su media de 200 sesiones; si no, efectivo.
  tendencia_inverso  igual, pero bajo la media compra el ETF inverso del S&P 500 (SH).
  momentum_top3      Jegadeesh-Titman: las 3 con mayor rentabilidad 12-1; vende al salir del top 10.
  momentum_filtro    momentum_top3 solo con el índice sobre su media de 200 (Antonacci, "dual momentum").
  rsi_reversion      en tendencia alcista (sobre media 200) compra sobreventa (RSI14 < 30), vende RSI > 55.
  cruce_medias       sobre medias de 50 y 200 y subiendo en el mes: compra (las 3 más fuertes); vende al perder la de 50.

Mismo mundo que el resto: simulador rápido idéntico al de ECONOSIM (comisión y tasas, horquilla,
factura diaria de Hetzner, tesorería), mismas vidas (16 por bloque, 40 empresas al azar por vida),
mismo índice de referencia.

Uso: training/.venv/Scripts/python.exe training/bots.py
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import laya_es as E                                                    # noqa: E402
from econosim.market.calendar import UTC                               # noqa: E402
from econosim.world import World                                       # noqa: E402

SESSIONS, LIVES, SUBSET, SEED = 125, 16, 40, 7
ETF = {"SPY", "QQQ", "DIA", "IWM"} | E.INVERSE
SELL, HOLD, BUY = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]


def main() -> int:
    md, fu = E.MarketData(), E.Fundamentals()
    grid = E.Grid(md)
    cache = E.build_num_cache(md, fu, grid, "all", SESSIONS)
    col = {n: i for i, n in enumerate(cache["names"])}
    M = cache["M"]

    def val(r, n):
        return float(M[r, col[n]]) if n in col else float("nan")

    # por fecha: rasgos de cada empresa (sin posición) y régimen del índice
    dia = {}
    for (sym, dk, held), r in cache["index"].items():
        if held != "0":
            continue
        m1y, m20 = val(r, "mom_1y"), val(r, "mom_20d")
        dia.setdefault(dk, {})[sym] = {
            "mom121": (1 + m1y / 100) / (1 + m20 / 100) - 1 if not (np.isnan(m1y) or np.isnan(m20)) else np.nan,
            "m20": m20, "sma50": val(r, "mom_vsSMA50"), "sma200": val(r, "mom_vsSMA200"), "rsi": val(r, "RSI")}
    empresas = sorted({s for d in dia.values() for s in d} - ETF)

    def alcista(dk):
        spy = dia.get(dk, {}).get("SPY")
        return spy is not None and not np.isnan(spy["sma200"]) and spy["sma200"] > 0

    def ranking(dk, sub, clave, filtro=lambda x: True):
        xs = [(s, v[clave]) for s, v in dia.get(dk, {}).items()
              if s in sub and s not in ETF and not np.isnan(v[clave]) and filtro(v)]
        return [s for s, _ in sorted(xs, key=lambda x: -x[1])]

    # ---- los bots: lookup(símbolo, día, ¿tiene?) -> probabilidades [vender, mantener, comprar]
    def bot_tendencia(inverso: bool):
        def mk(sub):
            def look(s, dk, held):
                up = alcista(dk)
                if s == "SPY":
                    return BUY if up else SELL
                if s == E.INV_SPY and inverso:
                    return SELL if up else BUY
                return None
            return look
        return mk

    def bot_momentum(filtro_tendencia: bool):
        def mk(sub):
            def look(s, dk, held):
                if s in ETF or s not in sub:
                    return None
                if filtro_tendencia and not alcista(dk):
                    return SELL
                rk = ranking(dk, sub, "mom121")
                if s in rk[:3]:
                    return BUY
                if held and s not in rk[:10]:
                    return SELL
                return HOLD
            return look
        return mk

    def bot_rsi(sub):
        def look(s, dk, held):
            if s in ETF or s not in sub:
                return None
            v = dia.get(dk, {}).get(s)
            if not v or np.isnan(v["rsi"]):
                return HOLD
            if held:
                return SELL if v["rsi"] > 55 or v["sma200"] < 0 else HOLD
            if v["sma200"] > 0 and v["rsi"] < 30:
                return [0, 0, 1.0 - v["rsi"] / 100]           # más sobrevendida = más convicción
            return HOLD
        return look

    def bot_cruce(sub):
        def look(s, dk, held):
            if s in ETF or s not in sub:
                return None
            v = dia.get(dk, {}).get(s)
            if not v or np.isnan(v["sma50"]):
                return HOLD
            if held:
                return SELL if v["sma50"] < 0 else HOLD
            fuertes = ranking(dk, sub, "m20", lambda x: x["sma50"] > 0 and x["sma200"] > 0 and x["m20"] > 0)
            return BUY if s in fuertes[:3] else HOLD
        return look

    def nucleo(satelite, top: int = 2):
        """75 % siempre en el S&P 500 (núcleo) y el resto en `top` apuestas del satélite."""
        def mk(sub):
            sat = satelite(sub)
            def look(s, dk, held):
                if s == "SPY":
                    return [0, 0, 9.0]                       # el núcleo se compra primero y no se vende
                return sat(s, dk, held)
            return look
        return mk

    def bot_momentum_n(n: int):
        def mk(sub):
            def look(s, dk, held):
                if s in ETF or s not in sub:
                    return None
                rk = ranking(dk, sub, "mom121")
                if s in rk[:n]:
                    return BUY
                if held and s not in rk[:n * 3]:
                    return SELL
                return HOLD
            return look
        return mk

    bots = {
        "tendencia_indice": (bot_tendencia(False), 1.0),
        "tendencia_inverso": (bot_tendencia(True), 1.0),
        "momentum_top3": (bot_momentum(False), 1 / 3),
        "momentum_filtro": (bot_momentum(True), 1 / 3),
        "rsi_reversion": (bot_rsi, 1 / 3),
        "cruce_medias": (bot_cruce, 1 / 3),
        "nucleo75_momentum2": (nucleo(bot_momentum_n(2)), 0.125),
    }
    pricing = World(datetime(2020, 1, 1, tzinfo=UTC)).load_pricing("hetzner")
    i0 = next(i for i, d in enumerate(grid.days) if d >= E.SPLITS["all"][0] and i % E.DECIDE_EVERY == 0)
    bloques = list(range(i0, len(grid.days) - SESSIONS - 1, SESSIONS))
    rng = random.Random(SEED)
    subs_por_bloque = {b: [set(rng.sample(empresas, SUBSET)) for _ in range(LIVES)] for b in bloques}
    base = E.FastSim(md, grid, pricing, 50.0, 0.0)
    indice = {b: base.run(b, SESSIONS, lambda *_: None, fixed="index")["final"] for b in bloques}
    efectivo = {b: base.run(b, SESSIONS, lambda *_: None, fixed="cash")["final"] for b in bloques}

    out = {"metodo": "reglas fijas publicadas, sin aprendizaje: todos los bloques son prueba limpia",
           "bloques": len(bloques), "vidas_por_bloque": LIVES, "empresas_por_vida": SUBSET, "bots": {}}
    for nombre, (mk, frac) in bots.items():
        sim = E.FastSim(md, grid, pricing, 50.0, 0.0, buy_frac=frac,
                        frac_by={"SPY": 0.75} if nombre.startswith("nucleo") else None)
        filas = []
        for b in bloques:
            finals = [sim.run(b, SESSIONS, mk(sub), track=True) for sub in subs_por_bloque[b]]
            ex = np.array([r["final"] for r in finals]) - indice[b]
            filas.append({"bloque": str(grid.days[b]), "exceso_mediana": round(float(np.median(ex)), 2),
                          "gana_vidas": round(float((ex > 0).mean()), 2),
                          "euros": round(float(np.mean([r["final"] for r in finals])), 2),
                          "ops": round(float(np.mean([r["trades"] for r in finals])), 1),
                          "twr": [r["twr"] for r in finals], "dias": [r["dias_invertido"] for r in finals]})
        med = np.array([f["exceso_mediana"] for f in filas])
        twr = np.array([x for f in filas for x in f["twr"]]); dias = np.array([x for f in filas for x in f["dias"]])
        m = dias > 0
        anual = (np.exp(np.log1p(twr[m]).sum() * 252 / dias[m].sum()) - 1) * 100 if m.any() else float("nan")
        examen = [f for f in filas if f["bloque"] >= "2022-01-01"]
        out["bots"][nombre] = {
            "bloques_gana_al_indice": f"{int((med > 0).sum())}/{len(med)}",
            "examen_2022_2026": f"{sum(1 for f in examen if f['exceso_mediana'] > 0)}/{len(examen)}",
            "exceso_medio_por_bloque_€": round(float(med.mean()), 2),
            "peor_bloque_€": round(float(med.min()), 2), "mejor_bloque_€": round(float(med.max()), 2),
            "rentabilidad_anual_sobre_lo_invertido_%": round(float(anual), 1),
            "tiempo_invertido_%": round(float(dias.mean() / SESSIONS * 100), 0),
            "operaciones_por_vida": round(float(np.mean([f["ops"] for f in filas])), 1),
            "por_bloque": [{k: v for k, v in f.items() if k not in ("twr", "dias")} for f in filas]}
        b = out["bots"][nombre]
        print(f"{nombre:18s} gana al índice {b['bloques_gana_al_indice']:>6s} bloques · examen 2022-26 {b['examen_2022_2026']:>4s} · "
              f"exceso medio {b['exceso_medio_por_bloque_€']:+.2f} € (peor {b['peor_bloque_€']:+.2f}, mejor {b['mejor_bloque_€']:+.2f}) · "
              f"{b['rentabilidad_anual_sobre_lo_invertido_%']:.1f}%/año invertido ({b['tiempo_invertido_%']:.0f}% del tiempo) · "
              f"{b['operaciones_por_vida']} ops", flush=True)
    # referencia: el propio índice del simulador y el S&P 500 puro, por bloques
    spy = md.series["SPY"]
    sp = [(spy.asof(grid.days[min(b + SESSIONS, len(grid.days) - 1)]).close / spy.asof(grid.days[b]).close) for b in bloques]
    out["sp500_puro_anual_%"] = round(float((np.prod(sp) ** (252 / (SESSIONS * len(bloques))) - 1) * 100), 1)
    print(f"S&P 500 puro (comprar y mantener): {out['sp500_puro_anual_%']}%/año en esos bloques")
    # la MISMA medida para el índice del simulador (con 50 €: comisiones y ventas para pagar el servidor)
    ix = [base.run(b, SESSIONS, lambda *_: None, fixed="index", track=True) for b in bloques]
    tw = np.array([r["twr"] for r in ix]); dd = np.array([r["dias_invertido"] for r in ix]); m = dd > 0
    out["indice_sim_anual_invertido_%"] = round(float((np.exp(np.log1p(tw[m]).sum() * 252 / dd[m].sum()) - 1) * 100), 1)
    print(f"Índice en el simulador (misma medida, con costes de 50 €): {out['indice_sim_anual_invertido_%']}%/año")
    run = HERE / "laya_runs" / ("bots-" + datetime.now().strftime("%Y%m%d-%H%M"))
    run.mkdir(parents=True, exist_ok=True)
    (run / "bots.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("guardado en", run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
