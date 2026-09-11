"""G7: nada de lo que ve la IA contiene el año real de los datos ni la fecha real del host."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import LiveNet, check, make_world
from econosim.clock import UTC
from econosim.fakenet.server import FakeNet

REAL = datetime(1993, 3, 5, 14, 0, tzinfo=UTC)          # viernes → mostrado 2021-03-05, viernes
HOST_TODAY = datetime.now()
forbidden = {str(REAL.year), str(REAL.year + 1), HOST_TODAY.strftime("%Y-%m-%d"), str(HOST_TODAY.year)}
check(str(REAL.year + 28) not in forbidden, "el test es ambiguo: el año mostrado coincide con uno prohibido")

w, h = make_world(REAL)
fn = FakeNet(hang_seconds=1)
fn.mount(h.host, h.app())
H = {"Host": h.host, "Authorization": "Bearer test-token"}
seen: list[tuple[str, str]] = []


def scan(label: str, text: str) -> None:
    seen.append((label, text))
    for bad in forbidden:
        check(bad not in text, f"{label}: contiene {bad!r}: {text[:300]}")


with LiveNet(fn) as net:
    U = net.url
    r = requests.post(U("/v1/servers"), headers=H, json={"name": "w", "server_type": "cx23", "image": "debian-12"})
    scan("create", r.text)
    scan("create-headers", json.dumps(dict(r.headers)))
    sid = r.json()["server"]["id"]
    w.advance(timedelta(days=40))       # cruza una factura
    requests.post(U(f"/v1/servers/{sid}/actions/poweroff"), headers=H)
    for path in ("/v1/server_types", "/v1/server_types/1", "/v1/locations", "/v1/datacenters", "/v1/images",
                 "/v1/pricing", "/v1/servers", f"/v1/servers/{sid}", f"/v1/servers/{sid}/actions",
                 "/v1/actions", "/v1/actions/1", "/v1/nope"):
        r = requests.get(U(path), headers=H)
        scan(path, r.text)
        scan(path + " headers", json.dumps(dict(r.headers)))
    r = requests.delete(U(f"/v1/servers/{sid}"), headers=H)
    scan("delete", r.text)
    # con token malo tampoco
    scan("401", requests.get(U("/v1/servers"), headers={"Host": h.host}).text)

# lo que el panel enseña sin modo debug tampoco lleva el año real
scan("state", json.dumps(w.state(include_real=False)))
for e in w.ledger.entries():
    scan("ledger.display_ts", e.display_ts)
    scan("ledger.concept", e.concept + " " + e.ref)
for inv in h.invoices:
    scan("invoice", json.dumps({k: v for k, v in inv.items() if k != "issued_real"}))

# y sí aparece el calendario desplazado, con el weekday correcto
created = [t for l, t in seen if l == "/v1/servers/2"][0]
iso = re.search(r'"created": "([^"]+)"', created).group(1)
shown = datetime.fromisoformat(iso)
check(shown.year == REAL.year + 28 and shown.weekday() == REAL.weekday(), f"created mostrado {iso}")

# control positivo: el escáner detecta una fuga real
try:
    scan("control", json.dumps({"created": REAL.isoformat()}))
    check(False, "el escáner no detecta el año real")
except SystemExit:
    pass
try:
    scan("control-host", HOST_TODAY.strftime("%Y-%m-%d"))
    check(False, "el escáner no detecta la fecha del host")
except SystemExit:
    pass

check(len(seen) >= 30, f"pocas respuestas escaneadas: {len(seen)}")
print("NODATES OK")
