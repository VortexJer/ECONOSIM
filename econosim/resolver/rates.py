"""Cargador y validador de las tablas de tasas base. Sin fuente = error."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "data" / "pricing" / "base_rates.json"

# campos de fuente obligatorios en cada categoría
_SOURCE_KEYS = {"what", "url", "study", "retrieved"}


@dataclass(frozen=True)
class Category:
    key: str
    raw: dict

    @property
    def label(self) -> str:
        return self.raw["label"]

    @property
    def source(self) -> dict:
        return self.raw["source"]

    def get(self, k: str, default=None):
        return self.raw.get(k, default)

    def __getitem__(self, k: str):
        return self.raw[k]


class BaseRates:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8"))
        self.currency = data.get("_currency", "USD")
        self.modulation = data["modulation"]
        self.categories: dict[str, Category] = {}
        for key, raw in data["categories"].items():
            self._validate(key, raw)
            self.categories[key] = Category(key, raw)

    @staticmethod
    def _validate(key: str, raw: dict) -> None:
        src = raw.get("source")
        if not isinstance(src, dict) or not _SOURCE_KEYS <= set(src):
            raise ValueError(f"categoría {key!r} sin fuente completa (faltan {_SOURCE_KEYS - set(src or {})})")
        if not str(src.get("url", "")).startswith("http"):
            raise ValueError(f"categoría {key!r}: la fuente no tiene URL")
        if not src.get("retrieved"):
            raise ValueError(f"categoría {key!r}: la fuente no tiene fecha")
        if "label" not in raw:
            raise ValueError(f"categoría {key!r} sin label")

    def keys(self) -> list[str]:
        return sorted(self.categories)

    def __getitem__(self, key: str) -> Category:
        return self.categories[key]

    def get(self, key: str) -> Optional[Category]:
        return self.categories.get(key)
