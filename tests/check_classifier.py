"""G2: el clasificador reduce texto libre a una ficha con la categoría y campos correctos."""
from __future__ import annotations

import json

from _common import check
from econosim.resolver.classify import classify
from econosim.resolver.rates import BaseRates

br = BaseRates()


def llm(script):
    """Modelo guionizado: devuelve el JSON dado (o lanza para probar el respaldo)."""
    def call(messages, model):
        if isinstance(script, Exception):
            raise script
        return json.dumps(script) if isinstance(script, dict) else script
    return call


# --- con LLM que etiqueta bien ------------------------------------------------
f = classify("Vendo una plantilla de Notion para autónomos a 9€",
             br, llm({"category": "digital_product", "title": "Plantilla Notion",
                      "price": 9, "initial_cost": 0, "recurring_cost_monthly": None,
                      "target_market": "autónomos", "has_deliverable": True}))
check(f.category == "digital_product" and f.price == 9.0 and f.has_deliverable, f.to_dict())
check(f.title == "Plantilla Notion" and f.target_market == "autónomos", "campos")

# --- saneado: precios negativos y calidad fuera de rango se corrigen ----------
f = classify("x", br, llm({"category": "freelance_service", "price": -50, "initial_cost": -3}))
f.quality = 99
f.sane()
check(f.price == 0.0 and f.initial_cost == 0.0 and f.quality == 10.0, "saneo")

# --- categoría inventada por el LLM -> cae al heurístico ----------------------
f = classify("monto una tienda de camisetas por dropshipping",
             br, llm({"category": "categoria_que_no_existe", "price": 20}))
check(f.category == "ecommerce_physical", f"heurístico debería dar ecommerce, dio {f.category}")

# --- LLM que falla -> heurístico de palabras clave ----------------------------
f = classify("quiero apostar 20€ al partido del domingo", br, llm(RuntimeError("timeout")))
check(f.category == "sports_betting", f"heurístico apuestas, dio {f.category}")
f = classify("lanzo un micro-saas de facturación por suscripción", br, llm(ValueError("bad")))
check(f.category == "saas_micro", f"heurístico saas, dio {f.category}")

# --- sin LLM: solo heurístico -------------------------------------------------
f = classify("escribo un ebook y lo vendo en PDF", br, None)
check(f.category == "digital_product", f"sin LLM debería clasificar por palabras: {f.category}")

# --- JSON envuelto en texto: se extrae ---------------------------------------
f = classify("algo", br, llm('Aquí tienes: {"category":"content_affiliate","title":"Blog"} ¡listo!'))
check(f.category == "content_affiliate" and f.title == "Blog", "extracción de JSON embebido")

# --- el LLM SOLO etiqueta: la ficha no lleva ningún euro de resultado ---------
f = classify("vendo un curso a 50", br, llm({"category": "digital_product", "price": 50,
                                             "revenue": 999999, "units_sold": 1000}))  # campos de dinero ignorados
d = f.to_dict()
check("revenue" not in d and "units_sold" not in d, "la ficha no debe llevar resultados económicos")
check(f.price == 50.0, "el precio sí se conserva (es un input, no un resultado)")

print("CLASSIFIER OK")
