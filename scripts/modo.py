"""Cambia el mundo entre SOLO INVERSIÓN (por defecto desde la fase 14) y COMPLETO.

La IA no debe saber que existe lo desactivado: /opt/agent monta la carpeta agent/
entera, así que el encargo (SYSTEM.md) y el catálogo (SERVICIOS.md) del modo apagado
viven FUERA de ella, en desactivado/agent/. Este script los intercambia. No borra nada.

    python scripts/modo.py            # dice qué modo está activo
    python scripts/modo.py inversor   # solo bolsa, opciones, indicadores y cuentas
    python scripts/modo.py completo   # vuelve la tienda, anuncios, dominios, correo, apuestas

Después, lanza el mundo acorde: inversor es el defecto; para completo, `--modo completo`
en training/*.py o ECONOSIM_SOLO_INVERSION=0 / `run.py --completo`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
OFF = ROOT / "desactivado" / "agent"
FILES = ("SYSTEM.md", "SERVICIOS.md")
# lo que delata el modo completo: si aparece en agent/, la IA sabría que existe
MARCAS_COMPLETO = ("api.stripe.com", "graph.facebook.com", "porkbun", "resend", "the-odds-api")


def activo() -> str:
    txt = " ".join((AGENT / f).read_text(encoding="utf-8") for f in FILES if (AGENT / f).exists())
    return "completo" if any(m in txt.lower() for m in MARCAS_COMPLETO) else "inversor"


def swap() -> None:
    tmp = ROOT / "desactivado" / "_swap"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        shutil.move(str(AGENT / f), str(tmp / f))
        shutil.move(str(OFF / f), str(AGENT / f))
        shutil.move(str(tmp / f), str(OFF / f))
    tmp.rmdir()


def main(argv: list[str]) -> int:
    actual = activo()
    if not argv:
        print(f"modo activo: {actual}")
        return 0
    pedido = argv[0]
    if pedido not in ("inversor", "completo"):
        print("uso: python scripts/modo.py [inversor|completo]")
        return 2
    if pedido == actual:
        print(f"ya está en modo {actual}")
        return 0
    if not all((OFF / f).exists() for f in FILES):
        print(f"faltan los ficheros del modo {pedido} en {OFF}")
        return 1
    swap()
    print(f"modo {activo()} activo" + (" — lanza con --modo completo" if pedido == "completo" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
