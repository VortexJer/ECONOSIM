"""Arranque del mundo: internet falso + DNS + gemelos + API de control.

    python -m econosim.run --real-start 1998-10-14 --http 80 --https 443 --dns 53 \
        --answer-ip 10.66.0.2 --ca-dir /ca --control 8080 --control-bind auto \
        --econet 10.66.0.0/24 --agent-env /shared/agent.env --speed 1
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import secrets
import socket
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .clock import UTC, OFFSET_YEARS as OFFSET_YEARS_DEFAULT
from .control import control_app, debug_enabled
from .fakenet.certs import ensure_certs, server_context
from .fakenet.dns import FakeDNS
from .fakenet.server import FakeNet
from .fakenet.sni_gate import SNIGate
from .twins.hetzner import HetznerTwin
from .twins.openrouter import OpenRouterTwin
from .twins.qonto import QontoTwin
from .twins.alpaca import AlpacaTwin
from .twins.fundamentals_api import FundamentalsTwin
from .twins.stripe import StripeTwin
from .twins.meta_ads import MetaAdsTwin
from .twins.google_ads import GoogleAdsTwin
from .twins.domains import DomainsTwin
from .twins.email import EmailTwin
from .twins.betting import BettingTwin
from .commerce.reputation import Reputation
from .commerce.competition import Competition
from .resolver.rates import BaseRates
from .resolver.engine import ActionResolver
from .judge.judge import Judge
from .judge.review import HumanReviewQueue
from .hostile.engine import HostileEngine
from .commerce.market_bridge import MarketBridge
from .market.data import MarketData
from .market.episode import make_mask
from .market.fundamentals import Fundamentals
from .market.calendar import MARKET_OPEN
from .live import LiveMask
from .upstream import HTTPUpstream, FakeUpstream
from .world import World


def build_world(real_start: Optional[datetime], initial_eur: float, ledger_path: str, hang: float,
                episode_seed: str = "ep-0", years: float = 5.0, market: bool = True,
                live: bool = False, sim_duration=None, idle_speed: float = 0.0,
                active_grace_s: float = 60.0, solo_inversion: bool = False,
                active_speed: float = 1.0) -> tuple[World, FakeNet]:
    if ledger_path != ":memory:":
        # un fichero por episodio: <dir>/<id>.sqlite
        Path(ledger_path).mkdir(parents=True, exist_ok=True)
        ledger_path = str(Path(ledger_path) / f"{secrets.token_hex(4)}.sqlite")
    md = mask = None
    if live:
        market = True                                   # en vivo siempre hay datos reales de mercado
    if market:
        md = MarketData()
        # En vivo: máscara identidad (datos REALES, sin desplazar el tiempo). Si no,
        # máscara de episodio (alias, indexado, arranque aleatorio con margen).
        # El episodio no arranca antes de que las empresas publiquen cuentas: si no, la
        # IA invertiría a ciegas y nunca aprendería a mirar los números.
        fund = Fundamentals()
        mask = LiveMask(md) if live else make_mask(md, episode_seed, years,
                                                   min_day=fund.cobertura_desde())
        real_start = datetime.combine(mask.start_day, MARKET_OPEN, UTC)   # arranca en la apertura
    # En vivo el calendario no se desplaza: la IA ve la fecha REAL de los datos.
    offset_years = 0 if live else OFFSET_YEARS_DEFAULT
    world = World(real_start, initial_eur=initial_eur, ledger_path=ledger_path,
                  episode_id=Path(ledger_path).stem if ledger_path != ":memory:" else None,
                  offset_years=offset_years, live=live,
                  sim_duration=sim_duration, idle_speed=idle_speed,
                  active_grace_s=active_grace_s, active_speed=active_speed)
    # Cada petición de la IA a cualquier gemelo marca "está trabajando": con eso el reloj
    # va a ×1 mientras piensa/actúa y comprime solo lo que duerme.
    fakenet = FakeNet(hang_seconds=hang, display_now=lambda: world.clock.display_now,
                      on_activity_begin=world.activity_begin, on_activity_end=world.activity_end)
    hetzner = HetznerTwin(world, token=os.environ.get("ECONOSIM_HCLOUD_TOKEN") or None)
    fakenet.mount(hetzner.host, hetzner.app())
    if os.environ.get("ECONOSIM_FAKE_UPSTREAM", "") not in ("", "0", "false"):
        # Upstream determinista (para pruebas del cableado del sandbox sin depender
        # del proveedor externo). Responde con una acción bash y luego cierra sesión.
        def _canned(payload):
            msgs = payload.get("messages", [])
            if any(m.get("role") == "tool" for m in msgs[-3:]):
                return ("Listo por hoy.", [{"name": "end_session", "arguments": {"wake_in_minutes": 30, "note": "ok"}}])
            return ("Reviso mi situación.", [{"name": "bash", "arguments": {"command": "date -u; echo vivo >> NOTES.md; ls -la"}}])
        upstream = FakeUpstream(_canned, prompt_tokens=800, completion_tokens=60)
    else:
        base, key = os.environ.get("ECONOSIM_UPSTREAM_BASE_URL", ""), os.environ.get("ECONOSIM_UPSTREAM_API_KEY", "")
        if not base or not key:
            raise SystemExit("faltan ECONOSIM_UPSTREAM_BASE_URL / ECONOSIM_UPSTREAM_API_KEY (proveedor LLM real)")
        # Cerebro local (Ollama, vLLM, servidor propio): ECONOSIM_UPSTREAM_MODEL fija el modelo
        # REAL que se envía (en vez de "auto") y damos margen de timeout para inferencia local.
        local_model = os.environ.get("ECONOSIM_UPSTREAM_MODEL") or None
        # Timeout por intento: las peticiones del agente son grandes (prompt ~2k tokens + hasta
        # 1500 de salida) y un proveedor compartido puede tardar >30 s incluso en frío. Con 30 s
        # cada llamada moría por timeout y el agente veía 502 en bucle.
        timeout = float(os.environ.get("ECONOSIM_UPSTREAM_TIMEOUT", "120"))
        if local_model:
            upstream = HTTPUpstream(base, key, timeout=max(timeout, 180.0), retry_budget_s=180.0,
                                    retry_gap_s=5.0, model_override=local_model)
        else:
            upstream = HTTPUpstream(base, key, timeout=timeout, retry_budget_s=timeout * 2, retry_gap_s=10.0)
    openrouter = OpenRouterTwin(world, upstream)
    fakenet.mount(openrouter.host, openrouter.app(), server_header="cloudflare")
    qonto = QontoTwin(world)
    fakenet.mount(qonto.host, qonto.app())
    if market and md is not None:
        alpaca = AlpacaTwin(world, md, mask)
        fakenet.mount(alpaca.host, alpaca.app())
        fakenet.mount(alpaca.data_host, alpaca.data_app())
        # los números de las empresas: cuentas, valoración, calendario y consenso
        fundamentals = FundamentalsTwin(world, md, mask, fund)
        fakenet.mount(fundamentals.host, fundamentals.app())
    # MODO INVERSOR: el mundo se queda con el banco, el servidor, el cerebro, el bróker y
    # los números de las empresas. Nada de tienda, anuncios, dominios, correo ni apuestas.
    # Sirve para aislar la variable: ¿lo hace mejor si SOLO puede invertir? No es el modo
    # normal (el contrato dice que el motor no enumera negocios); es un experimento con
    # nombre propio, y el gemelo que no existe sencillamente no está en la red.
    stripe = meta_ads = google_ads = domains = email = betting = None
    if not solo_inversion:
        stripe = StripeTwin(world)
        fakenet.mount(stripe.host, stripe.app())
        meta_ads = MetaAdsTwin(world)
        google_ads = GoogleAdsTwin(world)
        fakenet.mount(meta_ads.host, meta_ads.app())
        fakenet.mount(google_ads.host, google_ads.app())
        domains = DomainsTwin(world)
        email = EmailTwin(world)
        betting = BettingTwin(world)
        fakenet.mount(domains.host, domains.app())
        fakenet.mount(email.host, email.app())
        fakenet.mount(betting.host, betting.app())
    # el juez usa un modelo barato distinto del cerebro de la IA, por el mismo upstream.
    # Se invoca al resolver ventas (hilo del reloj) y desde handlers async: en ninguno hay
    # un event loop utilizable por el hilo, así que la corrutina corre en un loop DEDICADO
    # propio y se espera su resultado. (Antes usaba get_event_loop().run_until_complete, que
    # en el hilo del reloj fallaba en silencio y el juez caía siempre a la puntuación base.)
    import asyncio as _asyncio
    import threading as _threading
    _judge_loop = _asyncio.new_event_loop()
    _threading.Thread(target=_judge_loop.run_forever, name="econosim-judge-loop", daemon=True).start()

    def _judge_llm(system, user, model):
        fut = _asyncio.run_coroutine_threadsafe(
            upstream.chat({"model": model, "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}],
                           "max_tokens": 300, "temperature": 0}),
            _judge_loop)
        res = fut.result(timeout=120)
        return res["choices"][0]["message"].get("content", "")
    judge = Judge(_judge_llm, model=os.environ.get("ECONOSIM_JUDGE_MODEL", "auto"))
    review = HumanReviewQueue()
    reputation = Reputation()
    if not solo_inversion:
        anuncios = [meta_ads.mgr, google_ads.mgr]
        hostile = HostileEngine(world, reputation=reputation, stripe=stripe, email=email,
                                ads_managers=anuncios)
        hostile.start_adversaries()
        # el resolutor de acciones (ventas por Stripe, reputación, competencia, juez y hooks del mundo hostil)
        rates = BaseRates()
        resolver = ActionResolver(world, rates, stripe=stripe, reputation=reputation,
                       competition=Competition(rates, world.episode.id), judge=judge, review_queue=review,
                       ad_managers=anuncios, hostile=hostile)
        MarketBridge(world, resolver, stripe, judge=judge)   # cierra el bucle producto -> ventas
    world.register("judge_review", review)
    world.register("fakenet", fakenet)
    return world, fakenet


def parse_duration(text: str):
    """'12h' '1d' '30d' '1mo' '1y' -> timedelta. Vacío = sin horizonte."""
    from datetime import timedelta as _td
    t = (text or "").strip().lower()
    if not t:
        return None
    units = {"h": 1 / 24, "d": 1.0, "w": 7.0, "mo": 30.4375, "y": 365.25}
    for suf in ("mo", "h", "d", "w", "y"):        # 'mo' antes que 'm'/'o'
        if t.endswith(suf):
            try:
                n = float(t[: -len(suf)])
            except ValueError:
                raise SystemExit(f"duración inválida: {text!r} (ej. 12h, 1d, 30d, 1mo, 1y)")
            return _td(days=n * units[suf])
    raise SystemExit(f"duración inválida: {text!r} (ej. 12h, 1d, 30d, 1mo, 1y)")


def pick_outside_ip(econet: str) -> str:
    net = ipaddress.ip_network(econet)
    ips = socket.gethostbyname_ex(socket.gethostname())[2]
    outside = [ip for ip in ips if ipaddress.ip_address(ip) not in net]
    if not outside:
        raise SystemExit(f"no hay interfaz fuera de {econet}; no se enlaza la API de control")
    return outside[0]


async def main(argv=None) -> None:
    # Los mensajes de arranque llevan acentos y flechas; en una consola Windows
    # (cp1252) eso revienta con UnicodeEncodeError. Forzamos UTF-8 en la salida.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser()
    p.add_argument("--real-start", default="", help="fecha real (solo sin mercado); con mercado la fija el episodio")
    p.add_argument("--episode-seed", default="ep-0", help="semilla del episodio (arranque y máscara del mercado)")
    p.add_argument("--years", type=float, default=5.0, help="años de recorrido pedidos (margen del arranque)")
    p.add_argument("--no-market", action="store_true", help="arrancar sin mercado (solo real-start)")
    p.add_argument("--live", action="store_true",
                   help="modo en vivo: datos reales (sin máscara ni desfase), candado de egreso activo")
    p.add_argument("--initial-eur", type=float, default=50.0)
    p.add_argument("--ledger", default=":memory:", help="':memory:' o DIRECTORIO donde va un sqlite por episodio")
    p.add_argument("--http", type=int, default=80)
    p.add_argument("--https", type=int, default=0)
    p.add_argument("--dns", type=int, default=0)
    p.add_argument("--answer-ip", default="127.0.0.1", help="IP a la que resuelve todo el DNS falso")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--ca-dir", default="")
    p.add_argument("--control", type=int, default=8080)
    p.add_argument("--control-bind", default="127.0.0.1", help="IP o 'auto' (primera fuera de --econet)")
    p.add_argument("--econet", default="10.66.0.0/24")
    p.add_argument("--agent-env", default="", help="fichero donde dejar las credenciales del agente")
    p.add_argument("--faketime-file", default="", help="fichero libfaketime del sandbox (desfase horario)")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--sim-duration", default="",
                   help="cuánto dura el episodio en tiempo simulado: 12h, 1d, 30d, 1mo, 1y. "
                        "Vacío = sin límite (solo termina si muere)")
    p.add_argument("--idle-speed", type=float, default=0.0,
                   help="compresión mientras la IA DUERME (p.ej. 3600). Pensar y trabajar van "
                        "siempre a x1. 0 = velocidad constante clásica (--speed)")
    p.add_argument("--hang", type=float, default=300.0)
    p.add_argument("--solo-inversion", action="store_true",
                   help="mundo de inversor: banco, servidor, cerebro, bolsa y cuentas de empresas; "
                        "sin tienda, anuncios, dominios, correo ni apuestas")
    p.add_argument("--allow-past", action="store_true", help="permitir fecha mostrada anterior a hoy (tests)")
    a = p.parse_args(argv)

    live = a.live or os.environ.get("ECONOSIM_LIVE", "") not in ("", "0", "false")
    market = not a.no_market and os.environ.get("ECONOSIM_MARKET", "1") not in ("", "0", "false")
    seed = os.environ.get("ECONOSIM_EPISODE_SEED", a.episode_seed)
    years = float(os.environ.get("ECONOSIM_YEARS", a.years))
    real_start = datetime.fromisoformat(a.real_start).replace(tzinfo=UTC) if a.real_start else None
    if not market and not live and real_start is None:
        real_start = datetime(1998, 10, 14, 9, 30, tzinfo=UTC)
    sim_duration = parse_duration(os.environ.get("ECONOSIM_SIM_DURATION", a.sim_duration))
    idle_speed = float(os.environ.get("ECONOSIM_IDLE_SPEED", a.idle_speed))
    solo_inversion = a.solo_inversion or os.environ.get("ECONOSIM_SOLO_INVERSION", "") not in ("", "0", "false")
    if solo_inversion:
        market = True                                   # sin bolsa no hay modo inversor
    world, fakenet = build_world(real_start, a.initial_eur, a.ledger, a.hang,
                                 episode_seed=seed, years=years, market=market, live=live,
                                 sim_duration=sim_duration, idle_speed=idle_speed,
                  active_grace_s=float(os.environ.get("ECONOSIM_ACTIVE_GRACE", "15")),
                  solo_inversion=solo_inversion,
                  active_speed=float(os.environ.get("ECONOSIM_ACTIVE_SPEED", "1")))
    hetzner: HetznerTwin = world.twins["hetzner"]  # type: ignore[assignment]
    openrouter: OpenRouterTwin = world.twins["openrouter"]  # type: ignore[assignment]
    qonto: QontoTwin = world.twins["qonto"]  # type: ignore[assignment]
    # El calendario mostrado nunca puede quedar por detrás del real: los catálogos
    # (modelos, precios) son de hoy y delatarían "el futuro". Datos >= hoy-28 años.
    # En vivo no hay máscara temporal: la fecha real es legítima, no se comprueba.
    if not live and world.clock.display_now.date() < datetime.now(UTC).date() and not a.allow_past:
        raise SystemExit(f"la fecha mostrada {world.clock.display_iso()} es anterior a hoy; "
                         f"usa datos de >= {datetime.now(UTC).year - 28} o --allow-past")

    ssl_ctx = None
    gate = None
    if a.https and a.ca_dir:
        ca_crt, crt, key = ensure_certs(Path(a.ca_dir), fakenet.hosts())
        ssl_ctx = server_context(crt, key)
        if a.agent_env:   # la IA solo recibe el certificado público de la CA, nunca las claves
            pub = Path(a.agent_env).parent / "ca.crt"
            pub.parent.mkdir(parents=True, exist_ok=True)
            pub.write_bytes(ca_crt.read_bytes())
        # TLS real solo en loopback; en el puerto público va la puerta SNI que
        # descarta (cuelga) cualquier nombre que no sea un gemelo.
        await fakenet.start(a.bind, a.http, 8443, ssl_ctx, https_host="127.0.0.1")
        gate = SNIGate(lambda name: name in fakenet.twins, "127.0.0.1", 8443, hang_seconds=a.hang)
        await gate.start(a.bind, a.https)
        world.register("snigate", gate)
    else:
        await fakenet.start(a.bind, a.http)

    dns = None
    if a.dns:
        dns = FakeDNS(a.answer_ip, a.bind, a.dns)
        await dns.start()

    cbind = pick_outside_ip(a.econet) if a.control_bind == "auto" else a.control_bind
    ctl = web.AppRunner(control_app(world, os.environ.get("ECONOSIM_CONTROL_TOKEN", ""), debug_enabled()),
                        access_log=None)
    await ctl.setup()
    await web.TCPSite(ctl, cbind, a.control).start()

    if a.agent_env:
        Path(a.agent_env).parent.mkdir(parents=True, exist_ok=True)
        creds = (f"HCLOUD_TOKEN={hetzner.token}\n"
                 f"OPENROUTER_API_KEY={openrouter.api_key}\n"
                 f"QONTO_ORG_SLUG={qonto.org_slug}\n"
                 f"QONTO_SECRET_KEY={qonto.secret_key}\n")
        if "alpaca" in world.twins:
            al = world.twins["alpaca"]
            creds += (f"ALPACA_API_KEY_ID={al.key_id}\n"
                      f"ALPACA_API_SECRET_KEY={al.secret}\n")
        if "fundamentals" in world.twins:
            creds += f"FMP_API_KEY={world.twins['fundamentals'].api_key}\n"
        if "stripe" in world.twins:
            creds += f"STRIPE_SECRET_KEY={world.twins['stripe'].secret_key}\n"
        Path(a.agent_env).write_text(creds, encoding="utf-8")

    if solo_inversion:
        print("[econosim] MODO INVERSOR: solo banco, servidor, cerebro, bolsa y cuentas de empresas", flush=True)
    print(f"[econosim] episodio {world.episode.id} · fecha mostrada {world.clock.display_iso()} · "
          f"saldo {world.balance() / 100:.2f} EUR"
          + ("  ·· MODO EN VIVO: datos reales, candado de egreso ACTIVO (ninguna acción sale)" if live else ""),
          flush=True)
    print(f"[econosim] fakenet http:{a.http} https:{a.https or '-'} dns:{a.dns or '-'} → {fakenet.hosts()}",
          flush=True)
    print(f"[econosim] control http://{cbind}:{a.control} (debug={'on' if debug_enabled() else 'off'})", flush=True)
    if idle_speed > 0:
        act = float(os.environ.get("ECONOSIM_ACTIVE_SPEED", "1"))
        print(f"[econosim] reloj: x{act:g} mientras la IA piensa/actúa · x{idle_speed:g} mientras duerme"
              + ("" if act == 1 else "  ·· ACELERADO: pensar ya no cuesta tiempo"), flush=True)
    print(f"[econosim] horizonte: {world.clock.display_iso(world.sim_end_real)}" if world.sim_end_real
          else "[econosim] horizonte: sin límite (termina solo si muere)", flush=True)

    if a.faketime_file:
        ft = Path(a.faketime_file)
        ft.parent.mkdir(parents=True, exist_ok=True)
        last = [None]

        def publish_offset() -> None:
            off = int(world.faketime_offset())
            if off != last[0]:
                last[0] = off
                tmp = ft.with_suffix(".tmp")
                tmp.write_text(f"{off:+d}\n", encoding="ascii")
                tmp.replace(ft)

        publish_offset()
        world.on_tick.append(publish_offset)

    world.run(a.speed)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        world.stop()
        await fakenet.stop()
        if gate:
            await gate.stop()
        if dns:
            await dns.stop()
        await ctl.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
