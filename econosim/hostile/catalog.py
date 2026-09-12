"""Cargador y validador del catálogo de cagadas. Sin fuente = error."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SOURCE_KEYS = {"what", "url", "study", "retrieved"}


@dataclass(frozen=True)
class Incident:
    key: str
    raw: dict

    def get(self, k, default=None):
        return self.raw.get(k, default)

    @property
    def domain(self) -> str:
        return self.raw["domain"]

    @property
    def label(self) -> str:
        return self.raw["label"]

    @property
    def source(self) -> dict:
        return self.raw["source"]


class IncidentCatalog:
    def __init__(self, path: Optional[Path] = None, pricing_dir: Optional[Path] = None):
        if path is None:
            path = (pricing_dir or Path(__file__).resolve().parent.parent.parent / "data" / "pricing") / "incidents.json"
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.strictness = float(data.get("_detection_strictness", 1.0))
        self.incidents: dict[str, Incident] = {}
        for key, raw in data["incidents"].items():
            self._validate(key, raw)
            self.incidents[key] = Incident(key, raw)

    @staticmethod
    def _validate(key: str, raw: dict) -> None:
        for f in ("domain", "label", "base_detection"):
            if f not in raw:
                raise ValueError(f"incidente {key!r} sin {f}")
        src = raw.get("source")
        if not isinstance(src, dict) or not _SOURCE_KEYS <= set(src):
            raise ValueError(f"incidente {key!r} sin fuente completa")
        if not str(src.get("url", "")).startswith("http"):
            raise ValueError(f"incidente {key!r}: fuente sin URL")
        if not (0.0 <= raw["base_detection"] <= 1.0):
            raise ValueError(f"incidente {key!r}: base_detection fuera de rango")

    def keys(self) -> list[str]:
        return sorted(self.incidents)

    def domains(self) -> set[str]:
        return {i.domain for i in self.incidents.values()}

    def __getitem__(self, key: str) -> Incident:
        return self.incidents[key]

    def get(self, key: str) -> Optional[Incident]:
        return self.incidents.get(key)
