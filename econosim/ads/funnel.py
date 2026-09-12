"""El embudo publicitario: euros -> impresiones -> clics -> visitas.

  impresiones = gasto / CPM × 1000        (CPM ajustado por sector)
  clics       = impresiones × CTR × atractivo   (rendimientos decrecientes por fatiga)
  visitas     = clics                     (una visita por clic)

Rendimientos decrecientes: a medida que se acumulan impresiones sobre la misma
audiencia, el CTR efectivo baja (fatiga). Doblar el gasto NO dobla los clics.
El atractivo del anuncio (0.5-1.5, centrado en 1.0) multiplica el CTR, nunca la
conversión de base.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..resolver.rates import BaseRates  # solo para tipar categorías conocidas (no se usa aquí)


@dataclass
class AdBenchmarks:
    raw: dict

    @property
    def platforms(self) -> list[str]:
        return sorted(self.raw["platforms"])

    def platform(self, name: str) -> dict:
        return self.raw["platforms"][name]

    def sector_mult(self, category: str) -> dict:
        return self.raw["sector_multiplier"].get(category, self.raw["sector_multiplier"]["default"])


def load_benchmarks(pricing_dir: Path) -> AdBenchmarks:
    import json
    data = json.loads((pricing_dir / "ads.json").read_text(encoding="utf-8"))
    _validate(data)
    return AdBenchmarks(data)


def _validate(data: dict) -> None:
    src = data.get("_source", {})
    for k in ("meta_url", "google_url", "retrieved"):
        if not src.get(k):
            raise ValueError(f"ads.json: falta fuente {k}")
    for name, p in data["platforms"].items():
        for k in ("cpm_usd", "base_ctr", "base_conversion", "fatigue_half_impressions"):
            if k not in p:
                raise ValueError(f"ads.json: plataforma {name} sin {k}")
        if not str(src.get("meta_url" if name == "meta" else "google_url", "")).startswith("http"):
            raise ValueError(f"ads.json: plataforma {name} sin URL de fuente")


def ad_appeal(ctr_quality: float) -> float:
    """Atractivo del anuncio 0.5-1.5 a partir de una nota 0-10 (5 neutro -> 1.0)."""
    return 0.5 + max(0.0, min(10.0, ctr_quality)) / 10.0


def funnel(bench: AdBenchmarks, platform: str, category: str, spend_usd: float,
           prior_impressions: float = 0.0, appeal: float = 1.0) -> dict:
    """Convierte un gasto en impresiones/clics/visitas dado lo ya gastado antes.

    `prior_impressions` = impresiones acumuladas de la campaña (para la fatiga).
    Devuelve el incremento por este tramo de gasto."""
    p = bench.platform(platform)
    mult = bench.sector_mult(category)
    cpm = p["cpm_usd"] * mult["cpm"]
    if spend_usd <= 0 or cpm <= 0:
        return {"impressions": 0, "clicks": 0, "visits": 0, "cpm": cpm}
    impressions = spend_usd / cpm * 1000.0
    # CTR efectivo con fatiga: decae según las impresiones acumuladas (mitad en fatigue_half)
    half = float(p["fatigue_half_impressions"])
    mid = prior_impressions + impressions / 2.0
    fatigue = 1.0 / (1.0 + mid / half)
    ctr = p["base_ctr"] * mult["ctr"] * appeal * fatigue
    clicks = impressions * ctr
    return {"impressions": int(round(impressions)), "clicks": clicks, "visits": clicks,
            "cpm": round(cpm, 3), "effective_ctr": ctr, "fatigue": fatigue}


def reach_from_clicks(bench: AdBenchmarks, clicks: float) -> float:
    """Traduce clics acumulados en el 'alcance' que entiende el resolutor."""
    return clicks / 1000.0 * bench.raw.get("reach_per_1000_clicks", 1.0)
