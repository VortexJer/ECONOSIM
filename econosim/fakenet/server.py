"""Proxy del internet falso: enruta por Host a los gemelos; el resto, silencio."""
from __future__ import annotations

import asyncio
import ssl
from datetime import datetime
from email.utils import format_datetime
from typing import Callable, Optional

from functools import partial

from aiohttp import web
from aiohttp.web_urldispatcher import Domain, MatchedSubAppResource


class HostRule(Domain):
    """Como Domain, pero ignora el puerto y las mayúsculas del Host."""

    def match_domain(self, host: str) -> bool:
        return host.split(":")[0].lower() == self._domain


class FakeNet:
    def __init__(self, hang_seconds: float = 300.0, display_now: Optional[Callable[[], datetime]] = None,
                 on_activity_begin: Optional[Callable[[], None]] = None,
                 on_activity_end: Optional[Callable[[], None]] = None):
        self.hang_seconds = hang_seconds
        self.display_now = display_now            # reloj mostrado: la cabecera Date sale de aquí
        # Se avisa al ENTRAR y al SALIR de cada petición a un gemelo: es la señal de "la IA
        # está trabajando" del reloj consciente de actividad. Marcar también la salida es
        # imprescindible: una llamada al LLM puede tardar un minuto y durante TODO ese rato
        # la IA está pensando (si no, el mundo la creería ociosa y aceleraría a mitad).
        self.on_activity_begin = on_activity_begin
        self.on_activity_end = on_activity_end
        self.twins: dict[str, web.Application] = {}
        self.server_header: dict[str, str] = {}   # host → cabecera Server plausible
        self.dropped = 0            # peticiones a hosts desconocidos (para el panel)
        self.served = 0
        self._runner: Optional[web.AppRunner] = None
        self._sites: list[web.BaseSite] = []

    def mount(self, host: str, app: web.Application, server_header: str = "nginx") -> None:
        self.twins[host.lower()] = app
        self.server_header[host.lower()] = server_header

    def hosts(self) -> list[str]:
        return sorted(self.twins)

    def app(self) -> web.Application:
        # aiohttp solo consulta los sub-apps por dominio cuando ninguna ruta del
        # root casa por path, así que el root no tiene rutas: el filtro de host
        # va en un middleware y lo que no es gemelo se descarta antes de enrutar.
        root = web.Application(middlewares=[self._host_filter])
        for host, sub in self.twins.items():
            root._add_subapp(partial(MatchedSubAppResource, HostRule(host), sub), sub)
        return root

    @web.middleware
    async def _host_filter(self, request: web.Request, handler):
        host = request.headers.get("Host", "").split(":")[0].lower()
        if host not in self.twins:
            # un curl a un host inventado se queda colgado: para la IA es tiempo REAL
            # esperando (como en la realidad), así que cuenta como actividad (x1).
            if self.on_activity_begin is not None:
                self.on_activity_begin()
            try:
                return await self._drop(request)
            finally:
                if self.on_activity_end is not None:
                    self.on_activity_end()
        self.served += 1
        if self.on_activity_begin is not None:
            self.on_activity_begin()
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            self._mask_headers(host, exc.headers)
            raise
        finally:
            if self.on_activity_end is not None:
                self.on_activity_end()
        self._mask_headers(host, resp.headers)
        return resp

    def _mask_headers(self, host: str, headers) -> None:
        """aiohttp pondría la fecha real del host y su propio nombre: fuga directa."""
        if self.display_now is not None:
            headers["Date"] = format_datetime(self.display_now(), usegmt=True)
        headers["Server"] = self.server_header.get(host, "nginx")

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
                    https_port: Optional[int] = None, ssl_ctx: Optional[ssl.SSLContext] = None,
                    https_host: Optional[str] = None) -> None:
        """El TLS interno escucha en `https_host` (loopback): delante va el SNIGate."""
        self._runner = web.AppRunner(self.app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, http_port)
        await site.start()
        self._sites.append(site)
        if https_port and ssl_ctx:
            tls = web.TCPSite(self._runner, https_host or host, https_port, ssl_context=ssl_ctx)
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
