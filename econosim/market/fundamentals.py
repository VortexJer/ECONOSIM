"""Fundamentales reales con fecha de publicación (data/fundamentals/*.json).

La regla del módulo, y la que hace que esto sirva para entrenar: **nada se sirve antes
de haberse publicado**. Todo acceso pasa por un `day` y filtra `publicado <= day`. Si un
trimestre se cerró el 30 de junio pero se presentó el 5 de agosto, hasta el 5 de agosto
no existe. Sin esa disciplina, la IA aprendería a "predecir" con datos del futuro y no
ganaría un euro en la vida real.

Solo el motor ve este módulo; la IA lo consulta por HTTP a través del gemelo.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
FUND_DIR = ROOT / "data" / "fundamentals"

# Duración (en días) que distingue un dato trimestral de uno anual en XBRL.
TRIM = (80, 100)
ANUAL = (350, 380)


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


@dataclass(frozen=True)
class Hecho:
    campo: str
    unidad: str
    val: float
    inicio: Optional[date]     # None = dato de saldo (foto a una fecha)
    fin: date
    publicado: date            # el día en que el mercado pudo saberlo
    forma: str
    fy: Optional[int]
    fp: Optional[str]

    @property
    def dias(self) -> Optional[int]:
        return (self.fin - self.inicio).days if self.inicio else None

    def es(self, rango: tuple[int, int]) -> bool:
        d = self.dias
        return d is not None and rango[0] <= d <= rango[1]


class CompanyFacts:
    """Los hechos de UNA empresa, ordenados por fecha de publicación."""

    def __init__(self, symbol: str, cik: int, hechos: list[Hecho]):
        self.symbol = symbol
        self.cik = cik
        self.hechos = sorted(hechos, key=lambda h: h.publicado)
        self._pub = [h.publicado for h in self.hechos]

    def __len__(self) -> int:
        return len(self.hechos)

    @property
    def primer_dia(self) -> Optional[date]:
        return self._pub[0] if self._pub else None

    def publicados(self, day: date) -> list[Hecho]:
        """Todo lo que ya era público el día `day`. Aquí vive la regla anti-futuro."""
        return self.hechos[:bisect_right(self._pub, day)]

    def periodos(self, day: date, campo: str, rango: Optional[tuple[int, int]] = TRIM,
                 limite: int = 8) -> list[Hecho]:
        """Los últimos periodos publicados de un campo, del más reciente al más antiguo."""
        sel = [h for h in self.publicados(day)
               if h.campo == campo and (rango is None or h.es(rango))]
        # si el mismo periodo se publicó dos veces (reexpresión), vale el más reciente
        por_fin: dict[date, Hecho] = {}
        for h in sel:
            prev = por_fin.get(h.fin)
            if prev is None or h.publicado >= prev.publicado:
                por_fin[h.fin] = h
        return sorted(por_fin.values(), key=lambda h: h.fin, reverse=True)[:limite]

    def saldos(self, day: date, campo: str, limite: int = 8) -> list[Hecho]:
        """Datos de balance (fotos a una fecha), del más reciente al más antiguo."""
        sel = [h for h in self.publicados(day) if h.campo == campo and h.inicio is None]
        por_fin: dict[date, Hecho] = {}
        for h in sel:
            prev = por_fin.get(h.fin)
            if prev is None or h.publicado >= prev.publicado:
                por_fin[h.fin] = h
        return sorted(por_fin.values(), key=lambda h: h.fin, reverse=True)[:limite]

    def ultimo(self, day: date, campo: str, rango: Optional[tuple[int, int]] = TRIM) -> Optional[Hecho]:
        p = self.saldos(day, campo, 1) if rango is None else self.periodos(day, campo, rango, 1)
        return p[0] if p else None

    def ttm(self, day: date, campo: str) -> Optional[float]:
        """Suma de los cuatro últimos trimestres, y solo si son CONTIGUOS.

        Los bancos, por ejemplo, no publican el cuarto trimestre por separado: sumar los
        cuatro últimos que haya daría un año inventado con un trimestre repetido. Si no
        encajan, no hay 12 meses; que lo resuelva quien llame (ver `doce_meses`)."""
        q = self.periodos(day, campo, TRIM, 4)
        if len(q) < 4 or any(h.inicio is None for h in q):
            return None
        cubierto = (q[0].fin - q[-1].inicio).days
        if not (ANUAL[0] <= cubierto <= ANUAL[1]):
            return None
        # y sin huecos: cada trimestre empieza donde acaba el anterior (±5 días)
        for antes, despues in zip(q[1:], q[:-1]):
            if abs((despues.inicio - antes.fin).days) > 5:
                return None
        return sum(h.val for h in q)

    def anual(self, day: date, campo: str) -> Optional[Hecho]:
        a = self.periodos(day, campo, ANUAL, 1)
        return a[0] if a else None

    def doce_meses(self, day: date, campo: str) -> tuple[Optional[float], str]:
        """Cifra de doce meses y sobre qué base se ha calculado, que no es lo mismo:
        ('ultimos_12m') suma de trimestres, ('ultimo_ejercicio') el último año cerrado."""
        v = self.ttm(day, campo)
        if v is not None:
            return v, "ultimos_12m"
        a = self.anual(day, campo)
        return (a.val, "ultimo_ejercicio") if a else (None, "sin_datos")

    def ultima_publicacion(self, day: date) -> Optional[date]:
        p = self.publicados(day)
        return p[-1].publicado if p else None

    def proxima_publicacion(self, day: date) -> Optional[date]:
        """La siguiente presentación de cuentas. Es información PÚBLICA por adelantado:
        las empresas anuncian la fecha, y el histórico de publicaciones la hace previsible.
        No revela ninguna cifra, solo cuándo habrá volatilidad."""
        fut = [p for p in self._pub if p > day]
        return fut[0] if fut else None


class Fundamentals:
    """Todas las empresas con cuentas. El motor la carga una vez por episodio."""

    def __init__(self, fund_dir: Path = FUND_DIR):
        self.dir = fund_dir
        self.por_simbolo: dict[str, CompanyFacts] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.dir.glob("*.json")):
            if path.stem == "manifest":
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            hechos = []
            for h in raw["hechos"]:
                if h.get("val") is None or not h.get("fin"):
                    continue
                hechos.append(Hecho(campo=h["campo"], unidad=h["unidad"], val=float(h["val"]),
                                    inicio=_d(h.get("inicio")), fin=_d(h["fin"]),
                                    publicado=_d(h["publicado"]), forma=h["forma"],
                                    fy=h.get("fy"), fp=h.get("fp")))
            if hechos:
                self.por_simbolo[path.stem] = CompanyFacts(path.stem, int(raw.get("cik", 0)), hechos)

    def get(self, symbol: str) -> Optional[CompanyFacts]:
        return self.por_simbolo.get(symbol)

    @property
    def symbols(self) -> list[str]:
        return sorted(self.por_simbolo)

    def cobertura_desde(self, fraccion: float = 0.9) -> Optional[date]:
        """El primer día en que ya publica cuentas al menos `fraccion` de las empresas.

        Un episodio que arrancase antes dejaría a la IA operando casi a ciegas. No se
        exige el 100 %: una empresa recién salida a bolsa no tiene histórico, y eso pasa
        también en la realidad."""
        primeros = sorted(c.primer_dia for c in self.por_simbolo.values() if c.primer_dia)
        if not primeros:
            return None
        i = min(len(primeros) - 1, int(len(primeros) * fraccion))
        return primeros[i]
