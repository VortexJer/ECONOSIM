"""G1: el juez puntúa un entregable 0-10 con rúbrica, devuelve desglose y justificación."""
from __future__ import annotations

from _common import check
from econosim.judge.judge import Judge, Deliverable
from econosim.judge import rubric

BODY = "x" * 600      # contenido con tamaño suficiente para que se evalúe de verdad


def llm(dims, just="ok"):
    def call(system, user, model):
        # el juez SOLO ve el contenido del entregable, nunca el pitch
        assert "pitch" not in user.lower() and "CONTENIDO DEL ENTREGABLE" in user, "el juez no debe recibir el pitch"
        assert "ESTRICTO" in system, "falta la rúbrica estricta"
        return {"dimensions": dims, "justification": just}
    return call


# --- puntuación con desglose por dimensión -----------------------------------
good = {"completeness": 9, "usefulness": 8, "correctness": 9, "polish": 8, "differentiation": 7}
j = Judge(llm(good, "producto sólido y completo"), model="judge-x")
v = j.score(Deliverable(kind="plantilla", content=BODY, niche="digital_product"))
check(0 <= v.score <= 10, f"nota fuera de rango: {v.score}")
check(set(v.dimensions) == set(rubric.DIMENSIONS), "faltan dimensiones en el desglose")
# la nota es la media ponderada de las dimensiones
expected = sum(good[k] * w for k, w in rubric.DIMENSIONS.items())
check(abs(v.score - round(expected, 2)) < 0.01, f"nota {v.score} != media ponderada {expected:.2f}")
check(v.justification and v.model == "judge-x", "sin justificación/modelo")
check(v.score > 7, "un producto bueno debería pasar de 7")

# --- un entregable mediocre saca nota baja (rúbrica estricta) -----------------
poor = {"completeness": 3, "usefulness": 2, "correctness": 4, "polish": 2, "differentiation": 1}
v2 = Judge(llm(poor)).score(Deliverable(kind="texto", content=BODY))
check(v2.score < 4, f"un trabajo mediocre debería sacar <4, sacó {v2.score}")

# --- el modelo del juez es DISTINTO del cerebro de la IA (parametrizable) -----
seen_models = []
def track(system, user, model):
    seen_models.append(model)
    return {"dimensions": {k: 5 for k in rubric.DIMENSIONS}, "justification": "x"}
Judge(track, model="mistral-cheap-judge").score(Deliverable(kind="web", content=BODY))
check(seen_models == ["mistral-cheap-judge"], f"no usa el modelo del juez: {seen_models}")

# --- desglose y nota reproducibles con el mismo modelo guionizado -------------
a = Judge(llm(good)).score(Deliverable(kind="plantilla", content=BODY))
b = Judge(llm(good)).score(Deliverable(kind="plantilla", content=BODY))
check(a.score == b.score and a.dimensions == b.dimensions, "no reproducible")

print("JUDGE OK")
