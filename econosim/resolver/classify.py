"""Clasificador: texto libre de la IA -> Ficha. El LLM SOLO etiqueta (§2.2).

Usa un modelo barato (el upstream del mundo, NO el cerebro de la IA) para elegir
categoría y extraer campos. El resultado se sanea contra las categorías conocidas;
si el modelo devuelve basura o una categoría inventada, cae a un heurístico de
palabras clave. Los euros NUNCA salen de aquí: la ficha solo describe la acción.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .ficha import Ficha
from .rates import BaseRates

# heurístico de respaldo (red, no autoridad): palabras -> categoría
_KEYWORDS = {
    "digital_product": ["plantilla", "template", "ebook", "e-book", "preset", "curso", "pdf", "notion",
                        "descargable", "guía", "guide", "pack de", "lut"],
    "freelance_service": ["servicio", "freelance", "encargo", "a medida", "diseño para", "redacción",
                          "consultoría", "consulting", "logo", "edito", "programo para"],
    "ecommerce_physical": ["dropshipping", "tienda", "camiseta", "producto físico", "print on demand",
                          "print-on-demand", "envío", "stock", "shopify"],
    "saas_micro": ["saas", "suscripción", "app web", "herramienta online", "api de pago", "micro-saas",
                  "plataforma", "webapp"],
    "content_affiliate": ["blog", "afiliación", "afiliado", "newsletter", "canal", "youtube", "adsense",
                         "contenido", "seo"],
    "sports_betting": ["apuesta", "apostar", "bet", "casa de apuestas", "cuota", "parley", "combinada"],
    "retail_trading": ["trading", "bolsa", "acciones", "invertir en", "day trading", "cartera", "broker"],
}


def _keyword_category(text: str, known: list[str]) -> str:
    t = text.lower()
    best, score = known[0], -1
    for cat in known:
        hits = sum(1 for kw in _KEYWORDS.get(cat, []) if kw in t)
        if hits > score:
            best, score = cat, hits
    return best


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


PROMPT = """Eres un clasificador de acciones de negocio. Devuelve SOLO un objeto JSON con:
  category  (una de: {cats})
  title     (título corto)
  price     (precio unitario en USD que cobra, número o null)
  initial_cost (coste inicial en USD, número o null)
  recurring_cost_monthly (coste mensual recurrente en USD, número o null)
  target_market (a quién va dirigido, texto corto)
  has_deliverable (true si produce algo entregable/evaluable, si no false)
No expliques nada, no añadas texto fuera del JSON. Acción a clasificar:
\"\"\"{text}\"\"\""""


def classify(text: str, rates: BaseRates, classifier_llm=None,
             model: str = "cheap") -> Ficha:
    """Clasifica `text` a una Ficha. `classifier_llm(messages, model) -> dict` es
    una función que llama al modelo barato del mundo; si es None o falla, se usa
    el heurístico de palabras clave."""
    known = rates.keys()
    parsed: dict = {}
    if classifier_llm is not None:
        try:
            content = classifier_llm(
                [{"role": "user", "content": PROMPT.format(cats=", ".join(known), text=text[:2000])}],
                model)
            parsed = _extract_json(content)
        except Exception:
            parsed = {}

    category = parsed.get("category")
    if category not in rates.categories:
        category = _keyword_category(text, known)

    f = Ficha(
        category=category,
        title=str(parsed.get("title") or text[:60].strip()),
        price=_num(parsed.get("price")),
        initial_cost=_num(parsed.get("initial_cost")),
        recurring_cost_monthly=_num(parsed.get("recurring_cost_monthly")),
        target_market=str(parsed.get("target_market") or ""),
        has_deliverable=bool(parsed.get("has_deliverable", False)),
        raw_text=text,
    )
    return f.sane()


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
