"""Gemelos de APIs reales. Mismos endpoints, mismos esquemas, mismos errores.

Cada gemelo expone `host` (el dominio real al que suplanta) y `app()` (una
aiohttp.web.Application con sus rutas). El internet falso los monta por Host.
"""
