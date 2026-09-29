"""Simulador rápido de estrategias diarias sobre el panel (agresivo/datos.py).

Cada `h` días, al CIERRE: puntúa las empresas comprables con pesos sobre los indicadores (normalizados
entre empresas ese día), y una fórmula del mercado decide si ir A FAVOR (las k mejores, a partes iguales)
o EN CONTRA (fondo inverso SH, que sube cuando cae el S&P 500). Se mantiene hasta la siguiente revisión.
Costes: sin comisión (petición del dueño); medio diferencial de 5 pb en cada compra y venta.
Solo usa datos hasta el cierre del día de decisión; el retorno se cobra del cierre t al cierre t+h.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PANEL = Path(__file__).resolve().parent.parent / "data" / "agresivo" / "panel.npz"
MEDIO_DIFERENCIAL = 5e-4


@dataclass
class Estrategia:
    pesos: np.ndarray                 # uno por indicador
    k: int = 5                        # cuántas empresas a la vez
    h: int = 5                        # cada cuántos días de bolsa revisa
    pesos_mercado: np.ndarray = field(default_factory=lambda: np.zeros(0))   # uno por indicador de mercado
    umbral: float = -9.0              # si la fórmula del mercado cae por debajo, va en contra (SH)
    contra: float = 1.0               # fracción que pone en SH cuando va en contra (el resto, efectivo)


class Motor:
    def __init__(self, panel: Path = PANEL):
        z = np.load(panel, allow_pickle=False)
        self.fechas = z["fechas"].astype("datetime64[D]")
        self.anio = self.fechas.astype("datetime64[Y]").astype(int) + 1970
        self.nombres = [str(x) for x in z["nombres"]]
        self.regimen_nombres = [str(x) for x in z["regimen_nombres"]]
        precio = z["precio"].astype(np.float64)
        self.apto = z["apto"]
        # precio para cobrar retornos: si la empresa desaparece, se queda en su último precio
        self.precio = _rellena_adelante(precio)
        self.fondos = {str(n): z["fondos"][:, i].astype(np.float64) for i, n in enumerate(z["fondos_nombres"])}
        # indicadores normalizados entre las empresas comprables de cada día (sin dato = 0, neutro)
        ind = z["indicadores"]
        self.Z = np.zeros(ind.shape, dtype=np.float32)
        for j in range(ind.shape[0]):
            x = np.where(self.apto, ind[j], np.nan)
            mu = np.nanmean(x, axis=1, keepdims=True)
            sd = np.nanstd(x, axis=1, keepdims=True)
            zz = (x - mu) / np.where(sd > 0, sd, 1)
            self.Z[j] = np.nan_to_num(np.clip(zz, -4, 4), nan=0.0)
        reg = z["regimen"].astype(np.float64)
        entreno = (self.anio >= 2005) & (self.anio <= 2016)        # constantes de escala: solo del periodo de búsqueda
        mu, sd = np.nanmean(reg[entreno], axis=0), np.nanstd(reg[entreno], axis=0)
        self.R = np.nan_to_num((reg - mu) / np.where(sd > 0, sd, 1), nan=0.0)

    def indices(self, desde: int, hasta: int) -> np.ndarray:
        return np.where((self.anio >= desde) & (self.anio <= hasta))[0]

    def simula(self, e: Estrategia, desde: int, hasta: int, curva: bool = False):
        dias = self.indices(desde, hasta)
        puntos = np.tensordot(e.pesos.astype(np.float32), self.Z[:, dias[0]:dias[-1] + 1], axes=1)
        mercado = self.R[dias[0]:dias[-1] + 1] @ e.pesos_mercado if len(e.pesos_mercado) else np.zeros(len(dias))
        sh = self.fondos["SH"]
        valor, pesos_previos, idx_previos = 1.0, None, None
        historia = [(dias[0], 1.0)]
        t = dias[0]
        while t + e.h <= dias[-1]:
            i = t - dias[0]
            en_contra = mercado[i] < e.umbral
            if en_contra:
                idx = np.array([-1])                                  # -1 = SH
                w = np.array([e.contra])
                rel = np.array([sh[t + e.h] / sh[t]])
            else:
                s = np.where(self.apto[t], puntos[i], -np.inf)
                n = min(e.k, int(np.isfinite(s).sum()))
                idx = np.argpartition(-s, n - 1)[:n]
                w = np.full(n, 1.0 / n)
                rel = self.precio[t + e.h, idx] / self.precio[t, idx]
            # rotación: lo que cambia respecto a lo que se tenía (ya movido por el mercado) paga el diferencial
            rotacion = _rotacion(idx_previos, pesos_previos, idx, w)
            coste = rotacion * MEDIO_DIFERENCIAL
            bruto = float(np.sum(w * np.nan_to_num(rel, nan=1.0))) + (1 - w.sum())   # el resto, en efectivo
            valor *= (1 - coste) * bruto
            final = w * np.nan_to_num(rel, nan=1.0)
            pesos_previos, idx_previos = final / max(bruto, 1e-12), idx
            t += e.h
            historia.append((t, valor))
            if valor <= 0.01:
                break
        valor *= 1 - _rotacion(idx_previos, pesos_previos, np.array([], dtype=int), np.array([])) * MEDIO_DIFERENCIAL
        historia[-1] = (historia[-1][0], valor)
        anios = (dias[-1] - dias[0]) / 252
        res = {"final": valor, "anual": valor ** (1 / anios) - 1 if valor > 0 else -1.0}
        if curva:
            res["curva"] = historia
        return res

    def por_anio(self, e: Estrategia, desde: int, hasta: int) -> dict[int, float]:
        """Rentabilidad de cada año natural por separado (se reinicia cada 1 de enero)."""
        return {a: self.simula(e, a, a)["final"] - 1 for a in range(desde, hasta + 1)}

    def fondo_por_anio(self, nombre: str, desde: int, hasta: int) -> dict[int, float]:
        f = self.fondos[nombre]
        return {a: f[self.indices(a, a)[-1]] / f[self.indices(a, a)[0]] - 1 for a in range(desde, hasta + 1)}


def _rellena_adelante(x: np.ndarray) -> np.ndarray:
    y = x.copy()
    for i in range(1, len(y)):
        m = np.isnan(y[i])
        y[i, m] = y[i - 1, m]
    return y


def _rotacion(idx_a, w_a, idx_b, w_b) -> float:
    if idx_a is None:
        return float(np.sum(w_b))
    a = dict(zip(idx_a.tolist(), w_a.tolist()))
    b = dict(zip(idx_b.tolist(), w_b.tolist()))
    return float(sum(abs(b.get(k, 0.0) - a.get(k, 0.0)) for k in set(a) | set(b)))
