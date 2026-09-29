"""Busca la estrategia agresiva: ajusta el peso de cada indicador, cuántas empresas, cada cuánto rota y
cuándo apuesta en contra del mercado, maximizando la rentabilidad anual en 2005-2016. Elige entre las
finalistas con 2017-2021 (no vistos por la búsqueda). 2022-2026 queda sin tocar para el examen final.

    python -m agresivo.busca --muestras 6000 --procesos 6
"""
from __future__ import annotations

import argparse
import json
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from agresivo.motor import Estrategia, Motor

OUT = Path(__file__).resolve().parent.parent / "data" / "agresivo"
BUSCA, ELIGE = (2005, 2016), (2017, 2021)
KS = [1, 2, 3, 5, 8, 10, 15, 20]
HS = [1, 2, 3, 5, 10, 21]
_m: Motor | None = None


def _inicia():
    global _m
    _m = Motor()


def _evalua(p: dict) -> dict:
    e = _a_estrategia(p)
    r = _m.simula(e, *BUSCA)
    return {**p, "busca": r["anual"]}


def _elige(p: dict) -> dict:
    e = _a_estrategia(p)
    return {**p, "elige": _m.simula(e, *ELIGE)["anual"]}


def _a_estrategia(p: dict) -> Estrategia:
    return Estrategia(pesos=np.array(p["pesos"], dtype=np.float32), k=p["k"], h=p["h"],
                      pesos_mercado=np.array(p["pesos_mercado"]), umbral=p["umbral"], contra=p["contra"])


def azar(rng: np.random.Generator, n_ind: int, n_reg: int) -> dict:
    pesos = np.zeros(n_ind)
    activos = rng.choice(n_ind, size=rng.integers(1, 7), replace=False)
    pesos[activos] = rng.normal(size=len(activos))
    reg = np.zeros(n_reg)
    usa_contra = rng.random() < 0.6
    if usa_contra:
        act = rng.choice(n_reg, size=rng.integers(1, 4), replace=False)
        reg[act] = rng.normal(size=len(act))
    return {"pesos": pesos.round(3).tolist(), "k": int(rng.choice(KS)), "h": int(rng.choice(HS)),
            "pesos_mercado": reg.round(3).tolist(), "umbral": float(rng.uniform(-2, 1)) if usa_contra else -9.0,
            # en los días malos: 0 = no comprar nada (todo en efectivo), 0.5 = mitad en el fondo inverso, 1 = todo
            "contra": float(rng.choice([0.0, 0.5, 1.0]))}


def muta(rng: np.random.Generator, p: dict) -> dict:
    q = json.loads(json.dumps(p))
    w = np.array(q["pesos"]); w += rng.normal(scale=0.3, size=len(w)) * (rng.random(len(w)) < 0.3)
    q["pesos"] = w.round(3).tolist()
    if rng.random() < 0.3:
        q["k"] = int(rng.choice(KS))
    if rng.random() < 0.3:
        q["h"] = int(rng.choice(HS))
    if q["umbral"] > -9 and rng.random() < 0.5:
        r = np.array(q["pesos_mercado"]); r += rng.normal(scale=0.3, size=len(r)) * (r != 0)
        q["pesos_mercado"] = r.round(3).tolist(); q["umbral"] = float(q["umbral"] + rng.normal(scale=0.2))
    if q["umbral"] > -9 and rng.random() < 0.2:
        q["contra"] = float(rng.choice([0.0, 0.5, 1.0]))
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestras", type=int, default=6000)
    ap.add_argument("--rondas", type=int, default=4)
    ap.add_argument("--procesos", type=int, default=6)
    ap.add_argument("--semilla", type=int, default=7)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    m = Motor()
    n_ind, n_reg = len(m.nombres), len(m.regimen_nombres)
    rng = np.random.default_rng(a.semilla)
    t0 = time.time()
    with Pool(a.procesos, initializer=_inicia) as pool:
        todos = pool.map(_evalua, [azar(rng, n_ind, n_reg) for _ in range(a.muestras)], chunksize=20)
        print(f"azar: {len(todos)} probadas en {time.time() - t0:.0f} s; mejor {max(x['busca'] for x in todos):.1%}/año", flush=True)
        for ronda in range(a.rondas):
            elite = sorted(todos, key=lambda x: -x["busca"])[:40]
            hijos = [muta(rng, p) for p in elite for _ in range(25)]
            todos += pool.map(_evalua, hijos, chunksize=20)
            print(f"ronda {ronda + 1}: {len(todos)} probadas; mejor {max(x['busca'] for x in todos):.1%}/año", flush=True)
        finalistas = sorted(todos, key=lambda x: -x["busca"])[:200]
        finalistas = pool.map(_elige, finalistas)
    finalistas.sort(key=lambda x: -x["elige"])
    (OUT / "finalistas.json").write_text(json.dumps({"nombres": m.nombres, "regimen": m.regimen_nombres,
                                                     "probadas": len(todos), "finalistas": finalistas}, indent=1),
                                         encoding="utf-8")
    print("mejores por 2017-2021 (entre las 200 mejores de 2005-2016):")
    for p in finalistas[:10]:
        activos = {m.nombres[i]: w for i, w in enumerate(p["pesos"]) if w}
        malos = "no" if p["umbral"] <= -9 else {0.0: "efectivo", 0.5: "mitad SH", 1.0: "todo SH"}[p["contra"]]
        print(f"  busca {p['busca']:.1%} | elige {p['elige']:.1%} | k={p['k']} h={p['h']} días malos={malos} | {activos}")
    print(f"total {time.time() - t0:.0f} s -> {OUT / 'finalistas.json'}")


if __name__ == "__main__":
    main()
