"""Puerta TLS: mira el SNI del ClientHello antes de negociar nada.

Si el nombre es un gemelo, reenvía los bytes tal cual al servidor TLS interno
(que tiene certificado para ese nombre). Si no, deja la conexión abierta sin
contestar hasta agotar `hang_seconds` y la cierra. Así `https://google.com`
se comporta como detrás de un firewall que descarta (timeout), no como un
proxy que presenta un certificado falso (error TLS que delataría la
interposición).
"""
from __future__ import annotations

import asyncio
import struct
from typing import Callable, Optional


def parse_sni(data: bytes) -> Optional[str]:
    """Extrae server_name de un ClientHello TLS. None si no se puede."""
    try:
        if len(data) < 5 or data[0] != 0x16:
            return None
        rec_len = struct.unpack("!H", data[3:5])[0]
        hs = data[5:5 + rec_len]
        if len(hs) < 4 or hs[0] != 0x01:
            return None
        p = 4 + 2 + 32                        # tipo+len, versión, random
        sid_len = hs[p]; p += 1 + sid_len
        cs_len = struct.unpack("!H", hs[p:p + 2])[0]; p += 2 + cs_len
        cm_len = hs[p]; p += 1 + cm_len
        if p + 2 > len(hs):
            return None
        ext_len = struct.unpack("!H", hs[p:p + 2])[0]; p += 2
        end = p + ext_len
        while p + 4 <= end:
            etype, elen = struct.unpack("!HH", hs[p:p + 4]); p += 4
            if etype == 0:                    # server_name
                q = p + 2                     # list length
                if hs[q] != 0:
                    return None
                nlen = struct.unpack("!H", hs[q + 1:q + 3])[0]
                return hs[q + 3:q + 3 + nlen].decode("ascii", "replace").lower()
            p += elen
        return None
    except (IndexError, struct.error, UnicodeError):
        return None


class SNIGate:
    def __init__(self, allowed: Callable[[str], bool], upstream_host: str, upstream_port: int,
                 hang_seconds: float = 300.0):
        self.allowed = allowed
        self.upstream = (upstream_host, upstream_port)
        self.hang_seconds = hang_seconds
        self.forwarded = 0
        self.dropped = 0
        self._server: Optional[asyncio.AbstractServer] = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                first = await asyncio.wait_for(reader.read(4096), timeout=10)
            except asyncio.TimeoutError:
                first = b""
            sni = parse_sni(first)
            if not sni or not self.allowed(sni):
                self.dropped += 1
                await asyncio.sleep(self.hang_seconds)
                return
            self.forwarded += 1
            ur, uw = await asyncio.open_connection(*self.upstream)
            uw.write(first)
            await uw.drain()

            async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
                try:
                    while True:
                        chunk = await src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)
                        await dst.drain()
                except (ConnectionError, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        dst.close()
                    except Exception:
                        pass

            await asyncio.gather(pump(reader, uw), pump(ur, writer))
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def start(self, host: str = "0.0.0.0", port: int = 443) -> None:
        self._server = await asyncio.start_server(self._handle, host, port)

    def bound_port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
