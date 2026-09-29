"""Máscara del episodio: convierte precios/símbolos reales en lo que ve la IA.

Reglas (PROYECTO.md §9.3):
  * Símbolo real -> alias estable dentro del episodio, distinto entre episodios.
  * Cada precio se indexa a 100 en la barra de arranque del episodio, así que solo
    se ve la DINÁMICA (retornos), nunca el nivel absoluto que delataría la época.
  * Los retornos diarios se conservan EXACTOS -> las correlaciones entre símbolos
    quedan intactas.
  * La fecha la desplaza el reloj (+28 años); aquí no se toca.

Un alias es determinista dada la semilla del episodio: reproducible, pero un
atacante no puede invertirlo sin la semilla.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .data import Bar, MarketData, Series

INDEX_BASE = 100.0
# Los fondos dicen QUÉ son (como en cualquier bróker), no CUÁLES son: un inversor real sabe
# que SPY es un fondo del índice amplio. Sin esto la IA no podía distinguir el índice de una
# empresa (todos eran "XXX Corp."), y "índice + satélite" era imposible sin información oculta.
FUNDS = {
    "SPY": "Broad Market 500 Index ETF",
    "QQQ": "Tech-Heavy 100 Index ETF",
    "DIA": "Blue Chip 30 Index ETF",
    "IWM": "Small Cap 2000 Index ETF",
    "SH": "Short Broad Market 500 ETF (-1x daily)",
    "PSQ": "Short Tech-Heavy 100 ETF (-1x daily)",
    "DOG": "Short Blue Chip 30 ETF (-1x daily)",
    "RWM": "Short Small Cap 2000 ETF (-1x daily)",
    # otros mercados (lo que usan los fondos de tendencia multimercado) y sus inversos
    "EFA": "Developed Markets ex-US Index ETF",
    "EEM": "Emerging Markets Index ETF",
    "TLT": "Long-Term Treasury Bond ETF (20+ yr)",
    "IEF": "Intermediate Treasury Bond ETF (7-10 yr)",
    "SHY": "Short-Term Treasury Bond ETF (1-3 yr)",
    "LQD": "Investment Grade Corporate Bond ETF",
    "TIP": "Inflation-Protected Treasury Bond ETF",
    "GLD": "Gold Bullion ETF",
    "SLV": "Silver Bullion ETF",
    "DBC": "Broad Commodities Index ETF",
    "USO": "Crude Oil Futures ETF",
    "VNQ": "Real Estate (REIT) Index ETF",
    "UUP": "US Dollar Bullish Index ETF",
    "EFZ": "Short Developed Markets ex-US ETF (-1x daily)",
    "EUM": "Short Emerging Markets ETF (-1x daily)",
    "TBF": "Short Long-Term Treasury Bond ETF (-1x daily)",
}
# Nombres de fantasía: consonante+vocal, dos sílabas + sufijo numérico. No se
# parecen a tickers reales pero se leen como uno.
_SYLL = ["AC", "BEL", "COR", "DAX", "EON", "FIN", "GLO", "HEX", "ION", "JAD",
         "KOR", "LUX", "MEV", "NOX", "ORB", "PYX", "QUA", "RUN", "SOL", "TRI",
         "VEX", "WYN", "XAN", "YRO", "ZEN"]


def _alias(symbol: str, seed: str) -> str:
    h = hashlib.sha256(f"{seed}:{symbol}".encode()).digest()
    a, b, n = _SYLL[h[0] % len(_SYLL)], _SYLL[h[1] % len(_SYLL)], (h[2] % 89) + 10
    return f"{a}{b}-{n}"


@dataclass(frozen=True)
class MaskedBar:
    """Barra tal como la ve la IA: alias, precios indexados, sin fecha real aquí."""
    open: float
    high: float
    low: float
    close: float
    volume: int


class EpisodeMask:
    """Máscara ligada a un episodio (semilla + fecha real de arranque)."""

    def __init__(self, data: MarketData, seed: str, start_day: date,
                 symbols: Optional[list[str]] = None):
        self.data = data
        self.seed = seed
        self.start_day = start_day
        pedidos = symbols or data.symbols
        # Una empresa que aún no cotizaba el día de arranque NO existe en este episodio:
        # sin precio base no hay escala, y listarla dejaba huecos que reventaban al
        # preguntar por ella. Fuera es igual: una empresa sin salir a bolsa no se compra.
        self.real_symbols = [s for s in pedidos
                             if (b := data.series[s].asof(start_day)) is not None and b.close > 0]
        # alias <-> real, únicos
        self.alias_of: dict[str, str] = {}
        self.real_of: dict[str, str] = {}
        for sym in self.real_symbols:
            alias = _alias(sym, seed)
            while alias in self.real_of:                    # resolver colisión rara
                alias = _alias(alias + "!", seed)
            self.alias_of[sym] = alias
            self.real_of[alias] = sym
        # factor de indexado por símbolo: 100 / (cierre en la barra de arranque)
        self._factor: dict[str, float] = {}
        for sym in self.real_symbols:
            base = data.series[sym].asof(start_day)
            if base is not None and base.close > 0:
                self._factor[sym] = INDEX_BASE / base.close

    # ---- traducción de identidad ----------------------------------------
    def to_alias(self, real_symbol: str) -> str:
        return self.alias_of[real_symbol]

    def fund_name(self, alias: str) -> Optional[str]:
        """Nombre descriptivo si el alias es un fondo cotizado; None si es una empresa."""
        return FUNDS.get(self.to_real(alias) or "")

    def to_real(self, alias: str) -> Optional[str]:
        return self.real_of.get(alias)

    @property
    def aliases(self) -> list[str]:
        return sorted(self.real_of)

    # ---- traducción de precio -------------------------------------------
    def index_price(self, real_symbol: str, price: float) -> float:
        # Sin redondear: conserva los retornos EXACTOS. El redondeo de presentación
        # lo hace el gemelo al serializar el JSON (como haría un feed real).
        return price * self._factor[real_symbol]

    def unindex_price(self, real_symbol: str, masked_price: float) -> float:
        """Precio enmascarado -> precio real (para contabilizar internamente si hiciera falta)."""
        return masked_price / self._factor[real_symbol]

    def factor(self, real_symbol: str) -> float:
        return self._factor[real_symbol]

    def mask_amount(self, real_symbol: str, amount: float) -> float:
        """Cifras absolutas de las cuentas (ingresos, beneficio, deuda…) al MISMO factor
        que el precio. Así el PER, el margen, el crecimiento y cualquier ratio salen
        exactos, pero el tamaño de la empresa no la delata."""
        return amount * self._factor[real_symbol]

    def mask_bar(self, real_symbol: str, bar: Bar) -> MaskedBar:
        f = self._factor[real_symbol]
        return MaskedBar(open=bar.open * f, high=bar.high * f, low=bar.low * f,
                         close=bar.close * f, volume=bar.volume)

    def series_for(self, alias: str) -> Optional[Series]:
        real = self.to_real(alias)
        return self.data.series[real] if real else None
