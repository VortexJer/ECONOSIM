"""¿Ventaja o suerte? Para las finalistas, SIN mirar 2022-2026:
  1) listón de la suerte: estrategias al azar del mismo tipo (mismo k y h), ¿qué sacan en 2017-2021?
  2) estabilidad: 60 variaciones pequeñas de los pesos (±15 %), ¿se mantiene 2017-2021?
  3) año a año 2005-2021 y la peor caída desde máximos.
La elegida para el examen final es la de mejor MEDIANA bajo variaciones (no la de mejor número suelto).
    python -m agresivo.solidez
"""
from __future__ import annotations

import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from agresivo.busca import OUT, _a_estrategia, azar
from agresivo.motor import Motor

_m: Motor | None = None


def _inicia():
    global _m
    _m = Motor()


def _val(p: dict) -> float:
    return _m.simula(_a_estrategia(p), 2017, 2021)["anual"]


def _caida_max(curva) -> float:
    v = np.array([x[1] for x in curva]); pico = np.maximum.accumulate(v)
    return float((v / pico - 1).min())


def main() -> None:
    d = json.loads((OUT / "finalistas.json").read_text(encoding="utf-8"))
    fin = d["finalistas"][:15]
    m = Motor()
    rng = np.random.default_rng(123)
    informe = []
    with Pool(6, initializer=_inicia) as pool:
        for n, p in enumerate(fin):
            # 1) listón de la suerte con el mismo k y h
            azares = []
            for _ in range(200):
                q = azar(rng, len(m.nombres), len(m.regimen_nombres)); q.update(k=p["k"], h=p["h"])
                azares.append(q)
            suerte = np.array(pool.map(_val, azares))
            # 2) variaciones pequeñas de sus pesos
            variantes = []
            for _ in range(60):
                q = json.loads(json.dumps(p))
                w = np.array(q["pesos"]); q["pesos"] = (w * (1 + rng.normal(scale=0.15, size=len(w)))).tolist()
                variantes.append(q)
            estab = np.array(pool.map(_val, variantes))
            e = _a_estrategia(p)
            anios = m.por_anio(e, 2005, 2021)
            caida = _caida_max(m.simula(e, 2005, 2021, curva=True)["curva"])
            fila = {"n": n, "k": p["k"], "h": p["h"], "busca": p["busca"], "elige": p["elige"],
                    "suerte_p95": float(np.percentile(suerte, 95)), "suerte_max": float(suerte.max()),
                    "supera_a_suerte_%": float((suerte < p["elige"]).mean() * 100),
                    "variantes_mediana": float(np.median(estab)), "variantes_p10": float(np.percentile(estab, 10)),
                    "caida_max_2005_21": caida, "por_anio": anios}
            informe.append(fila)
            print(f"#{n} k={p['k']} h={p['h']} | busca {p['busca']:.1%} elige {p['elige']:.1%} | azar p95 {fila['suerte_p95']:.1%} "
                  f"max {fila['suerte_max']:.1%} (le gana al {fila['supera_a_suerte_%']:.0f} %) | variantes mediana "
                  f"{fila['variantes_mediana']:.1%} p10 {fila['variantes_p10']:.1%} | peor caída {caida:.0%}", flush=True)
    (OUT / "solidez.json").write_text(json.dumps(informe, indent=1), encoding="utf-8")
    mejor = max(informe, key=lambda f: f["variantes_mediana"])
    print(f"\nelegida para el examen: #{mejor['n']} (mediana bajo variaciones {mejor['variantes_mediana']:.1%})")
    print("año a año:", {a: f"{r:+.0%}" for a, r in mejor["por_anio"].items()})


if __name__ == "__main__":
    main()
