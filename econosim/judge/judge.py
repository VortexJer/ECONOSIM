"""El juez: puntúa un entregable con la rúbrica. Nota 0-10 + desglso + justificación."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import rubric


@dataclass
class Deliverable:
    kind: str                       # "web", "texto", "codigo", "plantilla"...
    content: str = ""               # el artefacto real (lo que juzga el juez)
    files: dict = field(default_factory=dict)   # nombre -> contenido
    niche: str = ""                 # categoría/nicho para comparar
    # OJO: aquí NO va el pitch de la IA; el juez no lo ve.

    def total_chars(self) -> int:
        return len(self.content) + sum(len(v) for v in self.files.values())


@dataclass
class Verdict:
    score: float                    # 0-10
    dimensions: dict                # dimensión -> nota
    justification: str
    capped: bool = False            # True si se capó por entregable trivial
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(x, lo=0.0, hi=10.0) -> float:
    try:
        return min(hi, max(lo, float(x)))
    except (TypeError, ValueError):
        return lo


class Judge:
    def __init__(self, judge_llm=None, model: str = "judge-cheap", base_score: float = 3.0):
        """`judge_llm(system, user, model) -> str|dict` llama al modelo del juez
        (distinto del cerebro de la IA). Si es None o falla, se usa `base_score`."""
        self.judge_llm = judge_llm
        self.model = model
        self.base_score = base_score

    def score(self, d: Deliverable) -> Verdict:
        # 1) entregable trivial/vacío -> nota capada, sin molestar al modelo
        if d.total_chars() < rubric.MIN_CONTENT_CHARS:
            dims = {k: rubric.TRIVIAL_CAP for k in rubric.DIMENSIONS}
            return Verdict(min(rubric.TRIVIAL_CAP, self.base_score), dims,
                           "Entregable vacío o mínimo: no hay producto que evaluar.",
                           capped=True, model=self.model)

        # 2) evaluación por el modelo del juez (solo ve el CONTENIDO, no el pitch)
        dims, justification = self._ask_model(d)

        # 3) media ponderada de las dimensiones
        score = sum(dims[k] * w for k, w in rubric.DIMENSIONS.items())
        score = _clamp(score)

        # 4) capa dura por tamaño: un esqueleto no pasa de TRIVIAL_CAP por muy bien
        #    que lo puntúe el modelo (defensa contra que lo engañen con poco contenido)
        capped = False
        if d.total_chars() < 2 * rubric.MIN_CONTENT_CHARS and score > rubric.TRIVIAL_CAP + 2:
            score = rubric.TRIVIAL_CAP + 2
            capped = True
        return Verdict(round(score, 2), {k: round(dims[k], 2) for k in dims},
                       justification, capped=capped, model=self.model)

    def _ask_model(self, d: Deliverable) -> tuple[dict, str]:
        content = (d.content or "")[:8000]
        files_note = ""
        if d.files:
            listing = ", ".join(f"{n} ({len(c)} car.)" for n, c in list(d.files.items())[:20])
            files_note = f"Archivos incluidos: {listing}\n"
        user = rubric.USER_TEMPLATE.format(niche=d.niche or "(sin especificar)", kind=d.kind,
                                           content=content, files_note=files_note)
        dims = {k: self.base_score for k in rubric.DIMENSIONS}
        justification = "Evaluación por defecto (sin modelo de juez disponible)."
        if self.judge_llm is not None:
            try:
                raw = self.judge_llm(rubric.SYSTEM, user, self.model)
                parsed = raw if isinstance(raw, dict) else _extract_json(raw)
                md = parsed.get("dimensions", {}) if isinstance(parsed, dict) else {}
                for k in rubric.DIMENSIONS:
                    if k in md:
                        dims[k] = _clamp(md[k])
                justification = str(parsed.get("justification", justification))[:400]
            except Exception:
                pass
        return dims, justification


def _extract_json(content: str) -> dict:
    if isinstance(content, dict):
        return content
    m = re.search(r"\{.*\}", str(content), re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}
