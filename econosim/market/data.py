"""Cargador del histórico real (data/market/*.csv) a series diarias en memoria."""
from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
MARKET_DIR = ROOT / "data" / "market"


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class Series:
    """Serie diaria OHLCV de UN símbolo real, indexable por fecha."""

    def __init__(self, symbol: str, bars: list[Bar]):
        self.symbol = symbol
        self.bars = bars
        self._days = [b.day for b in bars]
        self._by_day = {b.day: b for b in bars}

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def first_day(self) -> date:
        return self._days[0]

    @property
    def last_day(self) -> date:
        return self._days[-1]

    def on(self, day: date) -> Optional[Bar]:
        return self._by_day.get(day)

    def asof(self, day: date) -> Optional[Bar]:
        """La barra de `day` o, si ese día no cotiza, la última anterior."""
        i = bisect_right(self._days, day)
        return self.bars[i - 1] if i else None

    def index_of(self, day: date) -> int:
        """Posición de la barra <= day (para calcular márgenes de arranque)."""
        return bisect_right(self._days, day) - 1


class MarketData:
    """Todas las series reales cargadas del disco. Solo el motor la ve."""

    def __init__(self, market_dir: Path = MARKET_DIR):
        self.dir = market_dir
        self.series: dict[str, Series] = {}
        self._load()

    def _load(self) -> None:
        for csv_path in sorted(self.dir.glob("*.csv")):
            sym = csv_path.stem
            bars: list[Bar] = []
            with open(csv_path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bars.append(Bar(
                        day=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=int(row["volume"])))
            if bars:
                self.series[sym] = Series(sym, bars)
        if not self.series:
            raise RuntimeError(f"no hay CSVs de mercado en {self.dir}; corre scripts/fetch_market.py")

    @property
    def symbols(self) -> list[str]:
        return sorted(self.series)

    def trading_days(self, symbols: Optional[list[str]] = None) -> list[date]:
        """Unión ordenada de días de cotización de los símbolos dados (o todos)."""
        syms = symbols or self.symbols
        days: set[date] = set()
        for s in syms:
            days.update(self.series[s]._days)
        return sorted(days)
