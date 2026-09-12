"""G2: el juez ve el producto, no el pitch; entregable vacío = nota baja; saneo de basura."""
from __future__ import annotations

from _common import check
from econosim.judge.judge import Judge, Deliverable
from econosim.judge import rubric

TOP = {"completeness": 10, "usefulness": 10, "correctness": 10, "polish": 10, "differentiation": 10}


def llm(dims):
    return lambda system, user, model: {"dimensions": dims, "justification": "x"}


# --- entregable vacío -> nota baja aunque el modelo diga 10 -------------------
v = Judge(llm(TOP)).score(Deliverable(kind="web", content=""))
check(v.capped and v.score <= rubric.TRIVIAL_CAP, f"vacío debería capar la nota, sacó {v.score}")
v = Judge(llm(TOP)).score(Deliverable(kind="web", content="Compra ya!!"))   # mínimo
check(v.capped and v.score <= rubric.TRIVIAL_CAP, f"mínimo debería capar, sacó {v.score}")

# --- entregable pequeño (esqueleto) no pasa del cap aunque el modelo lo infle -
small = "a" * (rubric.MIN_CONTENT_CHARS + 20)     # supera el mínimo pero es pequeño
v = Judge(llm(TOP)).score(Deliverable(kind="texto", content=small))
check(v.score <= rubric.TRIVIAL_CAP + 2, f"un esqueleto no debería pasar de {rubric.TRIVIAL_CAP+2}, sacó {v.score}")

# --- pitch de marketing en el contenido NO sube la nota (el modelo lo ignora) --
# el juez recibe el contenido; comprobamos que el prompt le ordena ignorar el marketing
captured = {}
def capture(system, user, model):
    captured["system"] = system
    captured["user"] = user
    return {"dimensions": {k: 5 for k in rubric.DIMENSIONS}, "justification": "x"}
Judge(capture).score(Deliverable(kind="web", content="B" * 500, niche="ecommerce_physical"))
check("NUNCA la descripción" in captured["system"] or "pitch" in captured["system"].lower(), "el prompt no ordena ignorar el pitch")
check("marketing" in captured["system"].lower(), "el prompt no menciona ignorar el marketing")

# --- el modelo devuelve BASURA -> cae a la nota base, sin reventar -------------
for bad in [lambda s, u, m: "no soy JSON",
            lambda s, u, m: {"dimensions": {"completeness": "diez"}},   # tipo inválido
            lambda s, u, m: (_ for _ in ()).throw(RuntimeError("timeout")),
            lambda s, u, m: {}]:
    v = Judge(bad, base_score=3.0).score(Deliverable(kind="texto", content="C" * 600))
    check(0 <= v.score <= 10, f"nota fuera de rango con modelo roto: {v.score}")

# --- dimensiones fuera de rango se acotan ------------------------------------
v = Judge(llm({"completeness": 99, "usefulness": -5, "correctness": 8, "polish": 7, "differentiation": 6})).score(
    Deliverable(kind="codigo", content="D" * 600))
check(all(0 <= x <= 10 for x in v.dimensions.values()), "dimensiones sin acotar")

# --- sin modelo de juez: nota base, no cero ni excepción ----------------------
v = Judge(None, base_score=3.0).score(Deliverable(kind="texto", content="E" * 600))
check(abs(v.score - 3.0) < 0.5, f"sin modelo debería dar ~base, dio {v.score}")

print("JUDGE ROBUSTNESS OK")
