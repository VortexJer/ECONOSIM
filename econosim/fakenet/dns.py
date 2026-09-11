"""DNS mínimo: toda consulta A resuelve a la IP del proxy. AAAA: sin registros.

Así el sandbox "ve internet" (todo resuelve) pero cualquier host que no sea un
gemelo cae en el proxy y se queda sin respuesta. No es un DNS completo a
propósito: solo A/AAAA, sin recursión, sin cache.
"""
from __future__ import annotations

import asyncio
import socket
import struct
from typing import Optional


def _parse_qname(data: bytes, offset: int) -> tuple[str, int]:
    labels = []
    while True:
        length = data[offset]
        offset += 1
        if length == 0:
            break
        labels.append(data[offset:offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), offset


class FakeDNSProtocol(asyncio.DatagramProtocol):
    def __init__(self, answer_ip: str, ttl: int = 60):
        self.answer_ip = answer_ip
        self.ttl = ttl
        self.queries = 0
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            resp = self.build_response(data)
        except Exception:
            return
        if resp and self.transport:
            self.transport.sendto(resp, addr)

    def build_response(self, data: bytes) -> Optional[bytes]:
        if len(data) < 12:
            return None
        tid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        if qd < 1:
            return None
        qname, off = _parse_qname(data, 12)
        qtype, qclass = struct.unpack("!HH", data[off:off + 4])
        question = data[12:off + 4]
        self.queries += 1
        rflags = 0x8180  # respuesta, recursión disponible, NOERROR
        answers = b""
        ancount = 0
        if qtype in (1, 255) and qclass == 1:   # A o ANY
            answers = (b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, self.ttl, 4)
                       + socket.inet_aton(self.answer_ip))
            ancount = 1
        header = struct.pack("!HHHHHH", tid, rflags, 1, ancount, 0, 0)
        return header + question + answers


class FakeDNS:
    def __init__(self, answer_ip: str, host: str = "0.0.0.0", port: int = 53):
        self.answer_ip, self.host, self.port = answer_ip, host, port
        self.protocol: Optional[FakeDNSProtocol] = None
        self._transport = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: FakeDNSProtocol(self.answer_ip), local_addr=(self.host, self.port))

    def bound_port(self) -> int:
        return self._transport.get_extra_info("sockname")[1]

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
