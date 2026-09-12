"""Cola de evaluación humana: guarda cada veredicto del juez y permite puntuar a mano.

Sirve para CALIBRAR: comparar la nota del juez con la del humano y ver si el juez
es demasiado blando o duro. El humano no evalúa cada entregable (no escala); es el
patrón contra el que se mide el juez (§8.3).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from .judge import Deliverable, Verdict


@dataclass
class ReviewItem:
    id: str
    kind: str
    niche: str
    preview: str                    # primeras líneas del entregable (para el panel)
    judge_score: float
    judge_dimensions: dict
    judge_justification: str
    human_score: Optional[float] = None
    human_note: str = ""


class HumanReviewQueue:
    def __init__(self):
        self.items: dict[str, ReviewItem] = {}
        self._order: list[str] = []

    def register(self, item_id: str, deliverable: Deliverable, verdict: Verdict) -> ReviewItem:
        it = ReviewItem(id=item_id, kind=deliverable.kind, niche=deliverable.niche,
                        preview=(deliverable.content or "")[:280],
                        judge_score=verdict.score, judge_dimensions=verdict.dimensions,
                        judge_justification=verdict.justification)
        self.items[item_id] = it
        self._order.append(item_id)
        return it

    def pending(self) -> list[ReviewItem]:
        return [self.items[i] for i in self._order if self.items[i].human_score is None]

    def rate(self, item_id: str, score: float, note: str = "") -> ReviewItem:
        it = self.items[item_id]
        it.human_score = min(10.0, max(0.0, float(score)))
        it.human_note = note
        return it

    def calibration(self) -> dict:
        """Desviación juez-humano sobre los ítems puntuados por el humano."""
        rated = [it for it in self.items.values() if it.human_score is not None]
        if not rated:
            return {"n": 0, "mean_judge": None, "mean_human": None, "bias": None, "mae": None}
        diffs = [it.judge_score - it.human_score for it in rated]
        return {
            "n": len(rated),
            "mean_judge": round(statistics.mean(it.judge_score for it in rated), 3),
            "mean_human": round(statistics.mean(it.human_score for it in rated), 3),
            "bias": round(statistics.mean(diffs), 3),                     # + = el juez puntúa por encima
            "mae": round(statistics.mean(abs(x) for x in diffs), 3),
        }
