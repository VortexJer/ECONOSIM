"""Noticias por empresa, ya leídas por FinBERT (training/noticias.py -> data/news/scored.parquet).

La IA NO ve el titular: un titular delata la época y la empresa ("Lehman quiebra"), y un modelo
de lenguaje sabe historia. Ve lo que un lector neutral sacaría de él: tipo, tono y confianza, y
cuándo se supo. Solo noticias ya conocidas en la hora virtual (`conocida` <= ahora).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

SCORED = Path(__file__).resolve().parent.parent.parent / "data" / "news" / "scored.parquet"
TIPOS_EN = {"precio": "price_move", "analistas": "analyst_rating", "resultados": "earnings",
            "corporativa": "corporate_action", "legal": "legal_regulatory", "directivos": "management",
            "general": "general"}
TONO_EN = {"buena": "Positive", "mala": "Negative", "neutra": "Neutral"}


class NewsStore:
    _cache: Optional["NewsStore"] = None

    def __init__(self, path: Path = SCORED):
        self.ok = path.exists()
        self.by_sym: dict[str, dict] = {}
        if not self.ok:
            return
        import pandas as pd
        df = pd.read_parquet(path, columns=["symbol", "conocida", "tipo", "tono", "p_buena", "p_mala"])
        df = df.sort_values("conocida")
        for sym, g in df.groupby("symbol", sort=False):
            self.by_sym[sym] = {
                "t": g.conocida.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[s]"),
                "tipo": g.tipo.to_numpy(), "tono": g.tono.to_numpy(),
                "pb": g.p_buena.to_numpy(np.float32), "pm": g.p_mala.to_numpy(np.float32)}

    @classmethod
    def shared(cls) -> "NewsStore":
        if cls._cache is None:
            cls._cache = cls()
        return cls._cache

    def recent(self, real: str, now_real: datetime, limit: int = 20,
               since_real: Optional[datetime] = None) -> list[dict]:
        """Las `limit` más recientes ya conocidas en `now_real` (fecha real, UTC ingenua)."""
        d = self.by_sym.get(real)
        if d is None:
            return []
        hasta = np.searchsorted(d["t"], np.datetime64(now_real.replace(tzinfo=None), "s"), side="right")
        desde = 0 if since_real is None else np.searchsorted(d["t"], np.datetime64(since_real.replace(tzinfo=None), "s"))
        idx = range(hasta - 1, max(desde, hasta - limit) - 1, -1)
        return [{"t": d["t"][i].astype(datetime), "tipo": d["tipo"][i], "tono": d["tono"][i],
                 "p_buena": float(d["pb"][i]), "p_mala": float(d["pm"][i])} for i in idx]
