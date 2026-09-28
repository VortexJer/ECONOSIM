"""Demostraciones de un inversor que lo hace BIEN, para enseñar a la IA por imitación (SFT).

Qué es "bien" aquí (y qué NO): el profesor es un robot con una receta que solo usa lo que se
sabía ese día (núcleo 75 % en el fondo del índice amplio + satélite 2 × 12,5 % en las empresas
con mejor momentum 12-1, con veto por noticias malas si `--noticias veto`). NUNCA elige mirando
lo que pasó después: eso enseñaría a la IA a fingir que adivina, y fuera perdería el dinero.
Una vida puede salir mal por mala suerte y sigue siendo una buena demostración.

Cada vida corre contra los gemelos REALES en proceso (Alpaca, FMP, Qonto, Hetzner, con la
máscara del episodio): cada comando que se ve en la grabación se ejecuta de verdad y su salida
es la respuesta HTTP exacta. Los scripts de Python que escribe el robot se ejecutan también de
verdad, con `requests` enchufado a los gemelos. Formato de salida = el del agente
(agent/agent.py): mensajes system/user/assistant(tool_calls)/tool, una SESIÓN por ejemplo.

Fechas de arranque al azar entre 2005-03 y 2021-06 (el examen 2022-2026 no se toca).

Uso: training/.venv/Scripts/python.exe training/demos.py --lives 400 [--noticias veto|no]
     -> training/data/demos.jsonl  (+ demos_meta.jsonl con el resultado de cada vida)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import os
import random
import secrets
import sys
import traceback
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from aiohttp.test_utils import TestClient, TestServer                  # noqa: E402

from econosim.market.calendar import UTC                                # noqa: E402
from econosim.market.data import MarketData                             # noqa: E402
from econosim.market.fundamentals import Fundamentals                   # noqa: E402
from econosim.market.mask import EpisodeMask                            # noqa: E402
from econosim.twins.alpaca import SPREAD_BPS, AlpacaTwin                # noqa: E402
from econosim.twins.fundamentals_api import FundamentalsTwin            # noqa: E402
from econosim.twins.hetzner import HetznerTwin                          # noqa: E402
from econosim.twins.qonto import QontoTwin                              # noqa: E402
from econosim.world import World                                        # noqa: E402

SYSTEM_MD = ROOT / "agent" / "SYSTEM.md"
SERVICIOS_MD = ROOT / "agent" / "SERVICIOS.md"
HOME = "/home/agent"
NUCLEO, SATELITE, N_SAT, FUERA_DE = 0.75, 0.125, 2, 6
BANDA = 0.10                      # reequilibrar el núcleo si se desvía más de 10 puntos
CADA = 21                         # sesiones de bolsa entre revisiones (~1 mes)
RESERVA_DIAS = 60                 # días de servidor que se dejan en efectivo…
RESERVA_MIN = 0.02                # …y como poco el 2 % (el modelo con el que piensa también se paga)
HORA = time(15, 0)                # despierta con el mercado abierto (13:30-20:00 UTC)
# jq: solo los campos que sirven (menos tokens al pensar; jq está en la máquina de la IA)
CUENTA = ("cash", "equity", "buying_power")
POSICION = ("symbol", "qty", "avg_entry_price", "current_price", "market_value", "unrealized_plpc")
ORDEN = ("symbol", "side", "status", "filled_qty", "filled_avg_price", "commission", "message")

ALP = '-H "APCA-API-KEY-ID: $ALPACA_API_KEY_ID" -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET_KEY"'
TRADING, DATA = "https://api.alpaca.markets", "https://data.alpaca.markets"

RANKING_PY = r'''import os, datetime, requests

A = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET_KEY"]}
FMP = os.environ["FMP_API_KEY"]
hoy = requests.get("https://api.alpaca.markets/v2/clock", headers=A).json()["timestamp"][:10]
desde = (datetime.date.fromisoformat(hoy) - datetime.timedelta(days=30)).isoformat()
cartera = {p["symbol"]: p for p in requests.get("https://api.alpaca.markets/v2/positions", headers=A).json()}
fondos, empresas = [], []
for a in requests.get("https://api.alpaca.markets/v2/assets", headers=A).json():
    s = a["symbol"]
    barras = requests.get(f"https://data.alpaca.markets/v2/stocks/{s}/bars", headers=A,
                          params={"timeframe": "1Day", "limit": 260}).json()["bars"]
    c = [b["c"] for b in barras]
    if "ETF" in a["name"]:
        m = c[-1] / c[-253] - 1 if len(c) >= 253 else None
        fondos.append((s, a["name"], c[-1], m))
    elif len(c) >= 253:
        empresas.append((c[-22] / c[-253] - 1, s, c[-1]))   # momentum 12-1: 12 meses sin el ultimo
empresas.sort(reverse=True)
puesto = {s: i for i, (_, s, _) in enumerate(empresas, 1)}


def noticias(s):
    n = requests.get("https://financialmodelingprep.com/api/v3/stock_news",
                     params={"tickers": s, "from": desde, "limit": 50, "apikey": FMP}).json()
    n = [x for x in n if x["category"] != "price_move"]         # las que solo cuentan el precio no informan
    return sum(x["sentiment"] == "Positive" for x in n), sum(x["sentiment"] == "Negative" for x in n)


print(f"fecha {hoy} | {len(empresas)} empresas con 1 ano de historia")
print("FONDOS:")
for s, nombre, px, m in fondos:
    print(f"  {s:10s} {px:9.2f}  12m {m * 100:+6.1f}%  {nombre}" if m is not None else f"  {s:10s} {px:9.2f}  {nombre}")
print("MOMENTUM 12-1 (top 8 empresas) | noticias 30 dias sin contar las de precio:")
for i, (m, s, px) in enumerate(empresas[:8], 1):
    b, mal = noticias(s)
    print(f"  {i}. {s:10s} {px:9.2f}  mom {m * 100:+6.1f}%  noticias {b} buenas / {mal} malas")
print("TU CARTERA:")
for s, p in cartera.items():
    extra = f"puesto {puesto[s]}" if s in puesto else "fondo"
    if s in puesto:
        b, mal = noticias(s)
        extra += f", noticias {b} buenas / {mal} malas"
    print(f"  {s:10s} {float(p['qty']):.4f} x {float(p['current_price']):.2f} = {float(p['market_value']):.2f}  "
          f"({float(p['unrealized_plpc']) * 100:+.1f}%)  {extra}")
if not cartera:
    print("  (vacia)")
'''


# --------------------------------------------------------------------- red en proceso
class Red:
    """Los gemelos como servidores HTTP de verdad dentro del proceso (aiohttp test server)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, apps: dict):
        self.loop, self.clients = loop, {}
        async def arranca(app):
            c = TestClient(TestServer(app))           # se crea con el bucle ya corriendo
            await c.start_server()
            return c
        for host, app in apps.items():
            self.clients[host] = loop.run_until_complete(arranca(app))

    def pedir(self, method: str, url: str, headers=None, params=None, body=None) -> tuple[int, str]:
        u = urlsplit(url)
        q = dict(parse_qsl(u.query))
        q.update({k: str(v) for k, v in (params or {}).items()})

        async def go():
            r = await self.clients[u.hostname].request(method, u.path, headers=headers or {}, params=q, data=body)
            return r.status, await r.text()
        return self.loop.run_until_complete(go())

    def cerrar(self):
        for c in self.clients.values():
            self.loop.run_until_complete(c.close())


class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code, self.text, self.ok = status, text, status < 400

    def json(self):
        return json.loads(self.text)


def requests_falso(red: Red) -> types.ModuleType:
    m = types.ModuleType("requests")

    def get(url, headers=None, params=None, timeout=None, **_):
        return _Resp(*red.pedir("GET", url, headers, params))

    def post(url, headers=None, json=None, data=None, timeout=None, **_):
        h = dict(headers or {})
        if json is not None:
            h.setdefault("Content-Type", "application/json")
            data = __import__("json").dumps(json)
        return _Resp(*red.pedir("POST", url, h, None, data))
    m.get, m.post = get, post
    return m


# --------------------------------------------------------------------- una vida
class Vida:
    def __init__(self, md: MarketData, fund: Fundamentals, loop, start: date, capital: float,
                 sesiones: int, rng: random.Random, noticias: str):
        self.rng, self.noticias_modo, self.capital = rng, noticias, capital
        self.mask = EpisodeMask(md, secrets.token_hex(6), start)
        self.w = World(datetime.combine(self.mask.start_day, HORA, UTC), initial_eur=capital)
        self.h = HetznerTwin(self.w)
        self.q = QontoTwin(self.w)
        self.a = AlpacaTwin(self.w, md, self.mask)
        self.f = FundamentalsTwin(self.w, md, self.mask, fund)
        self.red = Red(loop, {self.h.host: self.h.app(), self.q.host: self.q.app(), self.a.host: self.a.app(),
                              self.a.data_host: self.a.data_app(), self.f.host: self.f.app()})
        self.env = {"ALPACA_API_KEY_ID": self.a.key_id, "ALPACA_API_SECRET_KEY": self.a.secret,
                    "FMP_API_KEY": self.f.api_key, "QONTO_ORG_SLUG": self.q.org_slug,
                    "QONTO_SECRET_KEY": self.q.secret_key, "HCLOUD_TOKEN": self.h.token}
        dias = self.a.cal.sessions_between(self.mask.start_day, self.mask.start_day + timedelta(days=int(sesiones * 1.6) + 10))
        self.dias = dias[: sesiones + 1]
        self.fondo = next(a for a in self.mask.aliases if self.mask.to_real(a) == "SPY")
        self.notas = ""
        self.script_escrito = False
        self.servicios_leido = False
        self.ejemplos: list[dict] = []
        self.hecho_total: list[str] = []
        self.errores = 0

    # ---- ejecutar lo que "escribe" el robot -----------------------------------------
    def curl(self, reqs: list[tuple]) -> str:
        """reqs = [(método, url, cuerpo|None, jq|None)] -> salida de los `curl -s ... | jq -c` encadenados.
        jq = (campos, "obj" | "each"): `jq -c '{a,b}'` o `jq -c '.[] | {a,b}'` (formato compacto de jq)."""
        out = []
        for method, url, body, jq in reqs:
            h = {}
            if "alpaca" in url:
                h = {"APCA-API-KEY-ID": self.a.key_id, "APCA-API-SECRET-KEY": self.a.secret}
            elif "qonto" in url:
                h = {"Authorization": f"{self.q.org_slug}:{self.q.secret_key}"}
            if body is not None:
                h["Content-Type"] = "application/json"
            url = url.replace("$FMP_API_KEY", self.f.api_key)
            _, text = self.red.pedir(method, url, h, None, body)
            if jq is None:
                out.append(text)
                continue
            campos, modo = jq
            dato = json.loads(text)
            objs = dato if modo == "each" else [dato]
            if modo == "each" and not isinstance(dato, list):
                objs = []                                    # jq fallaría; no ocurre con respuestas bien formadas
            out.extend(json.dumps({c: o.get(c) for c in campos}, ensure_ascii=False, separators=(",", ":"))
                       for o in objs)
        return "\n".join(out)

    @staticmethod
    def cmd_curl(method: str, url: str, body, jq) -> str:
        c = f"curl -s {ALP}" if "alpaca" in url else "curl -s"
        if body is not None:
            c += f" -H 'Content-Type: application/json' -X POST {url} -d '{body}'"
        else:
            c += f" {url}"
        if jq is None:
            return c + "; echo"
        campos, modo = jq
        filtro = "{" + ",".join(campos) + "}"
        return c + (f" | jq -c '.[] | {filtro}'" if modo == "each" else f" | jq -c '{filtro}'")

    def python(self, code: str) -> str:
        buf = io.StringIO()
        viejo = sys.modules.get("requests")
        sys.modules["requests"] = requests_falso(self.red)
        antes = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compile(code, f"{HOME}/ranking.py", "exec"), {"__name__": "__main__"})
        except SystemExit:
            pass
        except Exception:
            buf.write(traceback.format_exc(limit=2))
            self.errores += 1
        finally:
            if viejo is not None:
                sys.modules["requests"] = viejo
            else:
                sys.modules.pop("requests", None)
            for k, v in antes.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return buf.getvalue()

    @staticmethod
    def salida(texto: str) -> str:
        """Como agent.run_bash: recorta, recorta si es enorme y añade el código de salida."""
        texto = (texto or "").strip()
        if len(texto) > 8000:
            texto = texto[:4000] + "\n...[salida recortada]...\n" + texto[-3500:]
        return f"{texto}\n[exit=0]"

    # ---- conversación ------------------------------------------------------------------
    def _nueva_sesion(self) -> list[dict]:
        now = self.w.clock.display_now.strftime("%A %d de %B de %Y, %H:%M")
        system = SYSTEM_MD.read_text(encoding="utf-8") + \
            f"\n\n## Ahora\n\nFecha y hora del sistema: {now}. Directorio de trabajo: {HOME}."
        bloque = (f"\n\nTus notas de la sesión anterior (`/home/agent/NOTES.md`), ya leídas:\n\n"
                  f"```\n{self.notas}\n```\n") if self.notas else \
            "\n\nNo hay notas: es tu primera sesión, o las perdiste. Escribe `/home/agent/NOTES.md` antes de dormir.\n"
        user = ("Empieza una sesión nueva. No recuerdas nada anterior." + bloque +
                "No vuelvas a leer las notas ni a comprobar lo que ya dicen: ejecuta el PRÓXIMO PASO del PLAN "
                "(o crea el plan si no existe). Avanza al menos un paso hacia un ingreso, reescribe NOTES.md "
                "y termina con end_session.")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _paso(self, msgs: list, pensamiento: str, nombre: str, args: dict, resultado: str) -> None:
        cid = "call_" + secrets.token_hex(6)
        msgs.append({"role": "assistant", "content": pensamiento,
                     "tool_calls": [{"id": cid, "type": "function",
                                     "function": {"name": nombre, "arguments": json.dumps(args, ensure_ascii=False)}}]})
        if resultado is not None:
            msgs.append({"role": "tool", "tool_call_id": cid, "name": nombre, "content": resultado})

    def _elige(self, *frases: str) -> str:
        return self.rng.choice(frases)

    # ---- la receta ------------------------------------------------------------------------
    def _estado(self) -> tuple[float, float, dict]:
        cash = self.w.balance() / 100
        eq = self.a.equity_cents() / 100
        pos = {a: p for a, p in self.a.positions.items() if p.qty > 1e-9}
        return cash, eq, pos

    def _valor(self, alias: str) -> float:
        p = self.a.positions.get(alias)
        px = self.a._masked_price(alias)
        return p.qty * px if p and px else 0.0

    def _qty(self, alias: str, importe: float) -> float:
        px = self.a._masked_price(alias) * (1 + SPREAD_BPS / 1e4)
        fee = self.a._fees_cents("buy", importe / px, importe) / 100
        return math.floor(max(0.0, importe - fee) / px * 1e4) / 1e4

    def _parse_ranking(self, texto: str) -> tuple[list[tuple], dict]:
        top, cartera = [], {}
        zona = ""
        for ln in texto.splitlines():
            if ln.startswith("MOMENTUM"):
                zona = "top"; continue
            if ln.startswith("TU CARTERA"):
                zona = "cartera"; continue
            if ln.startswith("FONDOS"):
                zona = "fondos"; continue
            p = ln.split()
            if zona == "top" and p and p[0].rstrip(".").isdigit():
                top.append((p[1], int(p[p.index("noticias") + 1]), int(p[p.index("buenas") + 2])))
            elif zona == "cartera" and p and p[0] != "(vacia)":
                if "puesto" in ln:
                    k = p.index("puesto")
                    cartera[p[0]] = (int(p[k + 1].rstrip(",")), int(p[p.index("noticias") + 1]),
                                     int(p[p.index("buenas") + 2]))
                else:
                    cartera[p[0]] = None
        return top, cartera

    def sesion(self, n: int, siguiente: date | None) -> None:
        msgs = self._nueva_sesion()
        primera = not self.servicios_leido
        # 1) situación real (y la primera vez, leer los servicios)
        reqs = [("GET", f"{TRADING}/v2/clock", None, None),
                ("GET", f"{TRADING}/v2/account", None, (CUENTA, "obj")),
                ("GET", f"{TRADING}/v2/positions", None, (POSICION, "each"))]
        cmd = " && ".join(self.cmd_curl(*r) for r in reqs)
        if primera:
            cmd = "cat /opt/agent/SERVICIOS.md && " + cmd
            out = SERVICIOS_MD.read_text(encoding="utf-8").strip() + "\n" + self.curl(reqs)
            pens = self._elige(
                "Primera sesión y sin notas. Antes de decidir nada leo cómo funcionan los servicios y compruebo "
                "mi situación real: reloj del mercado, cuenta y posiciones.",
                "No tengo notas, así que empiezo de cero: leo la referencia de servicios y miro en un solo comando "
                "si el mercado está abierto, cuánto dinero hay y qué posiciones tengo.")
            self.servicios_leido = True
        else:
            out = self.curl(reqs)
            pens = self._elige(
                "Toca la revisión mensual que dejé apuntada. Compruebo en un solo comando reloj, cuenta y posiciones.",
                "Según mis notas hoy toca revisar. Primero la situación real: ¿mercado abierto?, saldo y posiciones.",
                "Revisión programada. Miro reloj, cuenta y cartera de una vez antes de decidir nada.")
        self._paso(msgs, pens, "bash", {"command": cmd}, self.salida(out))
        clock = json.loads(next(ln for ln in out.splitlines() if ln.startswith('{"timestamp"')))
        if not clock["is_open"]:
            self._cerrado(msgs, clock)
            return
        # 2) el ranking (la primera vez se escribe el script)
        if not self.script_escrito:
            cmd = f"cat > {HOME}/ranking.py <<'EOF'\n{RANKING_PY}EOF\npython3 {HOME}/ranking.py"
            pens = self._elige(
                "El mercado está abierto. Mi forma de invertir: el grueso (75 %) en el fondo del índice amplio, que "
                "recoge lo que gana el mercado con muy pocas comisiones, y un satélite pequeño (2 × 12,5 %) en las "
                "empresas con mejor momentum de 12 meses sin contar el último, que históricamente tiende a continuar. "
                "Para no gastar pasos, escribo un script que lo calcula todo de una vez (precios de un año de cada "
                "activo, momentum, noticias de los últimos 30 días y mi cartera) y lo dejo guardado para las próximas revisiones.",
                "Abierto. Voy a montar la cartera con una regla sencilla y comprobada: núcleo del 75 % en el fondo del "
                "índice amplio y dos empresas (12,5 % cada una) elegidas por momentum 12-1. Escribo un script reutilizable "
                "que baja un año de barras de cada activo, ordena por momentum, cuenta noticias buenas y malas del último mes "
                "y enseña mi cartera; así cada revisión es un solo comando.")
            self.script_escrito = True
        else:
            cmd = f"python3 {HOME}/ranking.py"
            pens = self._elige("Mercado abierto. Ejecuto mi script de ranking.",
                               "Abierto. Lanzo el ranking que tengo guardado en /home/agent/ranking.py.",
                               "Está abierto: corro el ranking para ver si el satélite sigue mereciendo su sitio.")
        rk = self.python(RANKING_PY)
        self._paso(msgs, pens, "bash", {"command": cmd}, self.salida(rk))
        top, cartera = self._parse_ranking(rk)
        # 3) decidir
        cash, eq, pos = self._estado()
        veto = self.noticias_modo == "veto"
        ventas, razones = [], []
        for alias, info in cartera.items():
            if info is None:
                continue
            puesto, b, m = info
            if puesto > FUERA_DE:
                ventas.append(alias); razones.append(f"{alias}, que ha caído al puesto {puesto} (fuera del top {FUERA_DE})")
            elif veto and m >= 2 and m > b:
                ventas.append(alias); razones.append(f"{alias}, que acumula {m} noticias malas frente a {b} buenas")
        quedan = [a for a in cartera if cartera[a] is not None and a not in ventas]
        compras, descartes = [], []
        for alias, b, m in top:
            if len(quedan) + len(compras) >= N_SAT:
                break
            if alias in quedan:
                continue
            if veto and m > b:
                descartes.append(f"{alias} (noticias {b} buenas / {m} malas)")
                continue
            compras.append(alias)
        reserva = max(RESERVA_DIAS * (self.h.types["cx23"]["monthly"] + self.h.pricing["primary_ipv4_monthly"]) / 30.4,
                      RESERVA_MIN * eq)
        v_fondo = self._valor(self.fondo)
        objetivo_fondo = NUCLEO * eq
        ajuste_fondo = objetivo_fondo - v_fondo if abs(v_fondo / eq - NUCLEO) > BANDA or v_fondo == 0 else 0.0
        # 4) órdenes: primero ventas, luego núcleo, luego satélite
        ordenes = []
        for alias in ventas:
            ordenes.append((alias, "sell", round(self.a.positions[alias].qty, 4)))
        libre = cash + sum(self._valor(a) for a in ventas) * (1 - SPREAD_BPS / 1e4) - reserva
        repone = 0.0
        if libre < 0 and ajuste_fondo >= 0 and v_fondo > 0:
            repone = min(v_fondo, -libre + reserva / 2)      # reponer la reserva (con margen) vendiendo núcleo
            ajuste_fondo = -repone
        if ajuste_fondo < 0:
            q = min(self.a.positions[self.fondo].qty, math.ceil(-ajuste_fondo / self.a._masked_price(self.fondo) * 1e4) / 1e4)
            if q > 0:
                ordenes.append((self.fondo, "sell", q)); libre += -ajuste_fondo
        compra_fondo = max(0.0, min(ajuste_fondo, libre - len(compras) * SATELITE * eq)) if ajuste_fondo > 0 else 0.0
        if compra_fondo > 20:
            ordenes.append((self.fondo, "buy", self._qty(self.fondo, compra_fondo))); libre -= compra_fondo
        for alias in compras:
            importe = min(SATELITE * eq, libre - 2)
            if importe > 20:
                ordenes.append((alias, "buy", self._qty(alias, importe))); libre -= importe
        ordenes = [o for o in ordenes if o[2] > 0]
        if not ordenes:
            pens = self._elige(
                f"Nada que cambiar: el núcleo está en {v_fondo / eq * 100:.0f} % (objetivo {NUCLEO * 100:.0f} %, banda ±{BANDA * 100:.0f}) "
                f"y el satélite sigue dentro del top {FUERA_DE}. No operar también es una decisión: rotar sin motivo solo paga comisiones.",
                f"La cartera sigue en regla (núcleo {v_fondo / eq * 100:.0f} %, satélite dentro del top {FUERA_DE}"
                f"{' y sin noticias malas acumuladas' if veto else ''}). No toco nada.")
            hecho = ["sin operaciones (cartera en regla)"]
        else:
            partes = []
            if razones:
                partes.append("Vendo " + "; ".join(razones) + ".")
            if descartes:
                partes.append("Salto " + ", ".join(descartes) + ": no compro con noticias malas recientes.")
            if repone:
                partes.append(f"El efectivo ({cash:.2f} €) no cubre la reserva para el servidor (~{reserva:.0f} €): "
                              f"vendo unos {repone:.0f} € del fondo del índice para reponerla.")
            elif compra_fondo > 20 or ajuste_fondo < 0:
                partes.append(f"El núcleo está en {v_fondo / eq * 100:.0f} % y el objetivo es {NUCLEO * 100:.0f} %: "
                              f"{'compro' if ajuste_fondo > 0 else 'vendo'} unos {abs(compra_fondo if ajuste_fondo > 0 else ajuste_fondo):.0f} € del fondo del índice ({self.fondo}).")
            if compras:
                partes.append("Satélite: compro " + " y ".join(compras) + f" (~{SATELITE * eq:.0f} € cada una), las primeras del ranking "
                              "que no tengo.")
            if not veto and self.rng.random() < 0.3:
                partes.append("Las noticias las miro, pero no deciden: su tono no anticipa el mes siguiente "
                              "(cuando salen, el precio ya las ha descontado).")
            partes.append(f"Dejo ~{reserva:.0f} € en efectivo para el servidor. Primero ventas (liberan caja), luego compras, "
                          "y al final compruebo posiciones y cuenta.")
            pens = " ".join(partes)
            reqs = [("POST", f"{TRADING}/v2/orders", json.dumps({"symbol": a, "qty": f"{q:g}", "side": s, "type": "market",
                                                                 "time_in_force": "day"}), (ORDEN, "obj")) for a, s, q in ordenes]
            reqs += [("GET", f"{TRADING}/v2/positions", None, (POSICION, "each")),
                     ("GET", f"{TRADING}/v2/account", None, (CUENTA, "obj"))]
            cmd = " && ".join(self.cmd_curl(*r) for r in reqs)
            out = self.curl(reqs)
            self._paso(msgs, pens, "bash", {"command": cmd}, self.salida(out))
            hecho = []
            for ln, (a, s, q) in zip(out.splitlines(), ordenes):
                o = json.loads(ln)
                if o.get("status") == "filled":
                    hecho.append(f"{'compra' if s == 'buy' else 'venta'} {o['filled_qty']} {a} @ {o['filled_avg_price']} (comisión {o['commission']})")
                else:
                    hecho.append(f"orden {s} {a} RECHAZADA: {o.get('message')}")
                    self.errores += 1
        self.hecho_total = hecho
        self._notas_y_dormir(msgs, siguiente, hecho, top)

    def _cerrado(self, msgs: list, clock: dict) -> None:
        abre = datetime.fromisoformat(clock["next_open"])
        ahora = datetime.fromisoformat(clock["timestamp"])
        mins = math.ceil((abre - ahora).total_seconds() / 60) + 5
        pens = self._elige(
            f"El mercado está cerrado (abre {clock['next_open']}). Mirar ahora no cambia nada y ejecutar no se puede: "
            f"actualizo la fecha de comprobación en las notas y duermo hasta la apertura.",
            f"Cerrado hasta {clock['next_open']}. Lo que tengo que hacer es operar, así que no gasto pasos: anoto y duermo "
            f"exactamente hasta que abra.")
        self.notas = self._reescribe_situacion(self.notas)
        self._paso(msgs, pens, "bash", {"command": f"cat > {HOME}/NOTES.md <<'EOF'\n{self.notas}\nEOF"}, self.salida(""))
        self._paso(msgs, "Notas al día. Duermo hasta la apertura.", "end_session",
                   {"wake_in_minutes": mins, "note": "mercado cerrado; duermo hasta la apertura"}, None)
        self.ejemplos.append({"messages": msgs})
        self.w.advance_to(abre.replace(year=abre.year - self.w.clock.offset_years) + timedelta(minutes=5))

    def _situacion(self) -> str:
        cash, eq, pos = self._estado()
        hoy = self.w.clock.display_now.strftime("%Y-%m-%d %H:%M UTC")
        cartera = "; ".join(f"{a}{' (fondo índice)' if a == self.fondo else ''} {p.qty:g} @ {p.avg:.2f}" for a, p in pos.items())
        return f"SITUACIÓN: patrimonio {eq:.2f} (efectivo {cash:.2f}); posiciones: {cartera or 'ninguna'}; comprobado {hoy}"

    def _reescribe_situacion(self, notas: str) -> str:
        return "\n".join(self._situacion() if ln.startswith("SITUACIÓN") else ln for ln in notas.splitlines())

    def _notas_y_dormir(self, msgs: list, siguiente: date | None, hecho: list, top: list) -> None:
        cuando = siguiente or (self.w.clock.real_now() + timedelta(days=30)).date()
        prox = self.w.clock.display(datetime.combine(cuando, HORA, UTC)).strftime("%Y-%m-%d")
        self.notas = (
            f"{self._situacion()}\n"
            f"TESIS: núcleo {NUCLEO * 100:.0f} % en el fondo del índice amplio ({self.fondo}): lo que gana el mercado, casi sin "
            f"comisiones. Satélite {N_SAT} × {str(SATELITE * 100).replace(".", ",")} % en las empresas con mejor momentum 12-1 (tiende a continuar). "
            f"Cambio de opinión: una del satélite sale del top {FUERA_DE}"
            f"{' o acumula noticias malas' if self.noticias_modo == 'veto' else ''}; el núcleo se reequilibra si se desvía más de "
            f"{BANDA * 100:.0f} puntos.\n"
            f"HECHO: {'; '.join(hecho)}\n"
            f"PRÓXIMO PASO: el {prox} con el mercado abierto: python3 {HOME}/ranking.py (ya escrito), vender lo que salga del top "
            f"{FUERA_DE}, reequilibrar el núcleo, reescribir estas notas. Nada que hacer antes: no revisar a diario.")
        cmd = f"cat > {HOME}/NOTES.md <<'EOF'\n{self.notas}\nEOF"
        pens = self._elige("Órdenes confirmadas en la respuesta. Reescribo las notas con lo que he visto confirmado.",
                           "Apunto la situación real, la tesis y el próximo paso en NOTES.md.",
                           "Dejo las notas al día antes de dormir.")
        if hecho == ["sin operaciones (cartera en regla)"]:
            pens = "Reescribo las notas con la fecha de esta comprobación y el próximo paso."
        self._paso(msgs, pens, "bash", {"command": cmd}, self.salida(""))
        if siguiente is None:
            mins = 60 * 24 * 30
        else:
            destino = datetime.combine(siguiente, HORA, UTC)
            if siguiente.weekday() == 0 and self.rng.random() < 0.5:
                # a veces despierta el sábado con el mercado cerrado: aprende a dormir hasta la apertura
                # (solo si la revisión cae en lunes, que es justo la siguiente apertura)
                destino = datetime.combine(siguiente - timedelta(days=2), time(10, 0), UTC)
            mins = max(30, math.ceil((destino - self.w.clock.real_now()).total_seconds() / 60))
        dias = mins / 1440
        self._paso(msgs, self._elige(f"Todo hecho. La tesis necesita semanas, no horas: duermo ~{dias:.0f} días hasta la próxima revisión.",
                                     f"Listo. Cierro la sesión y duermo {dias:.0f} días; revisar antes solo costaría dinero."),
                   "end_session", {"wake_in_minutes": mins, "note": "; ".join(hecho)[:200]}, None)
        self.ejemplos.append({"messages": msgs})
        if siguiente is not None:
            self.w.advance_to(self.w.clock.real_now() + timedelta(minutes=mins))

    def jugar(self) -> dict:
        revisiones = self.dias[::CADA]
        for k, d in enumerate(revisiones):
            if not self.w.alive:
                break
            if self.w.clock.real_now() < datetime.combine(d, HORA, UTC) and k == 0:
                self.w.advance_to(datetime.combine(d, HORA, UTC))
            sig = revisiones[k + 1] if k + 1 < len(revisiones) else None
            self.sesion(k, sig)
            while self.w.alive and sig is not None and self.w.clock.real_now().date() < sig:
                self.sesion(k, sig)          # despertó con el mercado cerrado: sesión corta y duerme hasta abrir
        fin = self.dias[-1]
        self.w.advance_to(datetime.combine(fin, time(21, 0), UTC))
        eq = self.a.equity_cents() / 100 if self.w.alive else 0.0
        spy = self.a.data.series["SPY"]
        idx = spy.asof(fin).close / spy.asof(self.mask.start_day).close
        self.red.cerrar()
        return {"start": str(self.mask.start_day), "final": round(eq, 2), "capital": self.capital,
                "rend": round(eq / self.capital - 1, 4), "indice": round(idx - 1, 4), "alive": self.w.alive,
                "sesiones": len(self.ejemplos), "errores": self.errores}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lives", type=int, default=400)
    ap.add_argument("--capital", type=float, default=5000.0)
    ap.add_argument("--sessions", type=int, default=126, help="sesiones de bolsa por vida (126 ≈ 6 meses)")
    ap.add_argument("--desde", default="2005-03-01")
    ap.add_argument("--hasta", default="2021-06-30", help="último arranque (el examen 2022-26 no se toca)")
    ap.add_argument("--noticias", choices=["veto", "no"], default="no")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=str(HERE / "data" / "demos.jsonl"))
    a = ap.parse_args()
    rng = random.Random(a.seed)
    md, fund = MarketData(), Fundamentals()
    dias = [d for d in md.series["SPY"].days() if date.fromisoformat(a.desde) <= d <= date.fromisoformat(a.hasta)] \
        if hasattr(md.series["SPY"], "days") else [b.day for b in md.series["SPY"].bars
                                                   if date.fromisoformat(a.desde) <= b.day <= date.fromisoformat(a.hasta)]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out.with_name(out.stem + "_meta.jsonl")
    n_ej = 0
    with open(out, "w", encoding="utf-8") as f, open(meta_path, "w", encoding="utf-8") as fm:
        for i in range(a.lives):
            v = Vida(md, fund, loop, rng.choice(dias), a.capital, a.sessions, rng, a.noticias)
            r = v.jugar()
            if r["errores"] == 0 and r["alive"]:       # proceso correcto: sin órdenes rechazadas ni scripts rotos
                for e in v.ejemplos:
                    f.write(json.dumps({**e, "vida": i, "start": r["start"]}, ensure_ascii=False) + "\n")
                    n_ej += 1
            fm.write(json.dumps({"vida": i, **r}, ensure_ascii=False) + "\n")
            print(f"vida {i + 1}/{a.lives} · {r['start']} · {r['rend'] * 100:+.1f} % (índice {r['indice'] * 100:+.1f} %) · "
                  f"{r['sesiones']} sesiones · errores {r['errores']}", flush=True)
    print(f"{n_ej} sesiones de demostración -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
