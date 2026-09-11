"""Proxy del internet falso: enruta por Host a los gemelos; el resto, silencio."""
from __future__ import annotations

import asyncio
import ssl
from typing import Optional

from aiohttp import web


class FakeNet:
    def __init__(self, hang_seconds: float = 300.0):
        self.hang_seconds = hang_seconds
        self.twins: dict[str, web.Application] = {}
        self.dropped = 0            # peticiones a hosts desconocidos (para el panel)
        self.served = 0
        self._runner: Optional[web.AppRunner] = None
        self._sites: list[web.BaseSite] = []

    def mount(self, host: str, app: web.Application) -> None:
        self.twins[host.lower()] = app

    def hosts(self) -> list[str]:
        return sorted(self.twins)

    def app(self) -> web.Application:
        root = web.Application()
        for host, sub in self.twins.items():
            root.add_domain(host, sub)
        root.router.add_route("*", "/{tail:.*}", self._drop)
        return root

    async def _drop(self, request: web.Request) -> web.StreamResponse:
        """Host desconocido: como un firewall que descarta paquetes. Sin respuesta."""
        self.dropped += 1
        try:
            await asyncio.sleep(self.hang_seconds)
        finally:
            if request.transport is not None:
                request.transport.close()
        raise web.HTTPRequestTimeout()  # nunca llega al cliente: el transporte ya está cerrado

    async def start(self, host: str = "0.0.0.0", http_port: int = 80,
                    https_port: Optional[int] = None, ssl_ctx: Optional[ssl.SSLContext] = None) -> None:
        self._runner = web.AppRunner(self.app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, http_port)
        await site.start()
        self._sites.append(site)
        if https_port and ssl_ctx:
            tls = web.TCPSite(self._runner, host, https_port, ssl_context=ssl_ctx)
            await tls.start()
            self._sites.append(tls)

    def bound_ports(self) -> list[int]:
        ports = []
        for s in self._sites:
            srv = getattr(s, "_server", None)
            if srv and srv.sockets:
                ports.append(srv.sockets[0].getsockname()[1])
        return ports

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
