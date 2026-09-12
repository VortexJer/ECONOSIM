"""G4: cola de revisión humana — registra, permite puntuar a mano, y calibra juez vs humano."""
from __future__ import annotations

from _common import check
from econosim.judge.judge import Judge, Deliverable, Verdict
from econosim.judge.review import HumanReviewQueue

q = HumanReviewQueue()


def mk(i, judge_score):
    d = Deliverable(kind="plantilla", content="contenido " * 40, niche="digital_product")
    v = Verdict(score=judge_score, dimensions={"completeness": judge_score}, justification="j")
    return q.register(f"it-{i}", d, v)


# --- registro y cola de pendientes -------------------------------------------
for i, s in enumerate([8.0, 3.0, 6.0, 9.0]):
    mk(i, s)
check(len(q.pending()) == 4, "no se registraron los 4 ítems")
it = q.items["it-0"]
check(it.judge_score == 8.0 and it.preview and it.kind == "plantilla", "ítem mal registrado")
check(q.calibration()["n"] == 0, "sin puntuaciones humanas no hay calibración")

# --- el humano puntúa a mano; sale de pendientes -----------------------------
q.rate("it-0", 6.0, note="bonito pero incompleto")
q.rate("it-1", 3.5)
q.rate("it-3", 7.0)
check(len(q.pending()) == 1 and q.pending()[0].id == "it-2", "la cola de pendientes no se actualiza")
check(q.items["it-0"].human_score == 6.0 and q.items["it-0"].human_note.startswith("bonito"), "no guarda la nota humana")

# la puntuación humana se acota 0-10
q.rate("it-2", 99)
check(q.items["it-2"].human_score == 10.0, "no acota la nota humana")

# --- calibración: sesgo y error medio juez vs humano -------------------------
cal = q.calibration()
# ítems puntuados: it-0 (8 vs 6), it-1 (3 vs 3.5), it-3 (9 vs 7), it-2 (6 vs 10)
check(cal["n"] == 4, f"n de calibración {cal['n']}")
diffs = [8 - 6, 3 - 3.5, 9 - 7, 6 - 10]
import statistics
check(abs(cal["bias"] - statistics.mean(diffs)) < 1e-6, f"sesgo mal calculado: {cal['bias']}")
check(abs(cal["mae"] - statistics.mean(abs(d) for d in diffs)) < 1e-6, f"MAE mal: {cal['mae']}")
check(cal["mean_judge"] is not None and cal["mean_human"] is not None, "medias ausentes")

# --- un juez sistemáticamente blando sale con sesgo positivo ------------------
q2 = HumanReviewQueue()
for i in range(10):
    d = Deliverable(kind="texto", content="x" * 300)
    q2.register(f"a-{i}", d, Verdict(score=8.0, dimensions={}, justification="j"))
    q2.rate(f"a-{i}", 5.0)      # el humano siempre puntúa 3 puntos menos
check(q2.calibration()["bias"] == 3.0, f"un juez blando debería dar sesgo +3, dio {q2.calibration()['bias']}")

print("HUMAN REVIEW OK")
