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
import socket
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .clock import UTC
from .control import control_app, debug_enabled
from .fakenet.certs import ensure_certs, server_context
from .fakenet.dns import FakeDNS
from .fakenet.server import FakeNet
from .twins.hetzner import HetznerTwin
from .world import World


def build_world(real_start: datetime, initial_eur: float, ledger_path: str, hang: float) -> tuple[World, FakeNet]:
    world = World(real_start, initial_eur=initial_eur, ledger_path=ledger_path)
    fakenet = FakeNet(hang_seconds=hang)
    hetzner = HetznerTwin(world, token=os.environ.get("ECONOSIM_HCLOUD_TOKEN") or None)
    fakenet.mount(hetzner.host, hetzner.app())
    world.register("fakenet", fakenet)
    return world, fakenet


def pick_outside_ip(econet: str) -> str:
    net = ipaddress.ip_network(econet)
    ips = socket.gethostbyname_ex(socket.gethostname())[2]
    outside = [ip for ip in ips if ipaddress.ip_address(ip) not in net]
    if not outside:
        raise SystemExit(f"no hay interfaz fuera de {econet}; no se enlaza la API de control")
    return outside[0]


async def main(argv=None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--real-start", required=True, help="fecha real de los datos, YYYY-MM-DD[THH:MM]")
    p.add_argument("--initial-eur", type=float, default=50.0)
    p.add_argument("--ledger", default=":memory:")
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
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--hang", type=float, default=300.0)
    a = p.parse_args(argv)

    real_start = datetime.fromisoformat(a.real_start).replace(tzinfo=UTC)
    world, fakenet = build_world(real_start, a.initial_eur, a.ledger, a.hang)
    hetzner: HetznerTwin = world.twins["hetzner"]  # type: ignore[assignment]

    ssl_ctx = None
    if a.https and a.ca_dir:
        _, crt, key = ensure_certs(Path(a.ca_dir), fakenet.hosts())
        ssl_ctx = server_context(crt, key)
    await fakenet.start(a.bind, a.http, a.https or None, ssl_ctx)

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
        Path(a.agent_env).write_text(f"HCLOUD_TOKEN={hetzner.token}\n", encoding="utf-8")

    print(f"[econosim] episodio {world.episode.id} · fecha mostrada {world.clock.display_iso()} · "
          f"saldo {world.balance() / 100:.2f} EUR", flush=True)
    print(f"[econosim] fakenet http:{a.http} https:{a.https or '-'} dns:{a.dns or '-'} → {fakenet.hosts()}",
          flush=True)
    print(f"[econosim] control http://{cbind}:{a.control} (debug={'on' if debug_enabled() else 'off'})", flush=True)

    world.run(a.speed)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        world.stop()
        await fakenet.stop()
        if dns:
            await dns.stop()
        await ctl.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
