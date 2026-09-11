"""G6: el proxy sirve a los gemelos y deja colgado todo lo demás; el DNS resuelve todo a la IP del proxy."""
from __future__ import annotations

import asyncio
import socket
import struct
import time

import requests

from _common import LiveNet, check, make_world
from econosim.fakenet.dns import FakeDNS, FakeDNSProtocol
from econosim.fakenet.server import FakeNet

w, h = make_world()
fn = FakeNet(hang_seconds=3, display_now=lambda: w.clock.display_now)
fn.mount(h.host, h.app())
AUTH = {"Authorization": "Bearer test-token"}

with LiveNet(fn) as net:
    U = net.url
    # control positivo: el mismo cliente, el mismo timeout, un host gemelo → responde
    t0 = time.time()
    r = requests.get(U("/v1/locations"), headers={"Host": "api.hetzner.cloud", **AUTH}, timeout=1)
    check(r.status_code == 200 and time.time() - t0 < 1, "el gemelo no respondió en <1 s")
    # mayúsculas y puerto en Host se normalizan
    r = requests.get(U("/v1/locations"), headers={"Host": f"API.Hetzner.Cloud:{net.port}", **AUTH}, timeout=1)
    check(r.status_code == 200, "Host con mayúsculas/puerto")
    # ruta inexistente en un gemelo → 404 del gemelo, no cuelgue
    r = requests.get(U("/v1/nope"), headers={"Host": "api.hetzner.cloud", **AUTH}, timeout=1)
    check(r.status_code == 404, f"ruta inexistente: {r.status_code}")

    # hosts desconocidos → sin respuesta hasta que el cliente se rinde
    for host in ("google.com", "api.openai.com", "example.com", "8.8.8.8", "localhost", ""):
        t0 = time.time()
        try:
            requests.get(U("/"), headers={"Host": host}, timeout=1)
            check(False, f"{host!r} recibió respuesta")
        except requests.exceptions.ReadTimeout:
            pass
        except requests.exceptions.ConnectionError as e:
            check(False, f"{host!r}: conexión rechazada en vez de colgada: {e}")
        check(0.9 < time.time() - t0 < 2.5, f"{host!r}: no agotó el timeout del cliente")
    check(fn.dropped == 6 and fn.served == 3, f"contadores dropped={fn.dropped} served={fn.served}")

    # tras el hang el servidor cierra sin responder (cliente paciente)
    t0 = time.time()
    try:
        requests.get(U("/"), headers={"Host": "github.com"}, timeout=10)
        check(False, "cliente paciente recibió respuesta")
    except requests.exceptions.ConnectionError:
        pass
    check(2.5 < time.time() - t0 < 6, "el cierre no ocurrió al acabar el hang")


# --- DNS ---------------------------------------------------------------------------
def query(name: str, qtype: int = 1) -> bytes:
    q = b"".join(bytes([len(l)]) + l.encode() for l in name.split(".")) + b"\x00"
    return struct.pack("!HHHHHH", 0xBEEF, 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", qtype, 1)


proto = FakeDNSProtocol("10.66.0.2")
resp = proto.build_response(query("google.com"))
tid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", resp[:12])
check(tid == 0xBEEF and flags & 0x8000 and (flags & 0xF) == 0 and an == 1, "cabecera A")
check(resp.endswith(socket.inet_aton("10.66.0.2")), "respuesta A no apunta al proxy")
resp = proto.build_response(query("api.hetzner.cloud", 28))     # AAAA
check(struct.unpack("!HHHHHH", resp[:12])[3] == 0, "AAAA debería venir vacío")
check(proto.build_response(b"\x00" * 5) is None, "basura no revienta")


async def dns_live() -> None:
    d = FakeDNS("10.66.0.2", "127.0.0.1", 0)
    await d.start()
    port = d.bound_port()
    loop = asyncio.get_running_loop()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    for name in ("google.com", "api.hetzner.cloud", "nonexistent.invalid", "x" * 60 + ".com"):
        s.sendto(query(name), ("127.0.0.1", port))
        data, _ = await loop.run_in_executor(None, s.recvfrom, 512)   # sin bloquear el loop del servidor
        check(data.endswith(socket.inet_aton("10.66.0.2")), f"{name} no resolvió al proxy")
    check(d.protocol.queries == 4, "contador de consultas")
    s.close()
    await d.stop()


asyncio.run(dns_live())
print("FAKENET OK")
