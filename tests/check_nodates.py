"""G7: nada de lo que ve la IA contiene el año real de los datos ni la fecha real del host."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import requests

from _common import LiveNet, check, make_world
from econosim.clock import UTC
from econosim.fakenet.server import FakeNet

REAL = datetime(1999, 3, 5, 14, 0, tzinfo=UTC)          # viernes → mostrado 2027-03-05, viernes
HOST_TODAY = datetime.now()
# el año del host puede aparecer legítimamente (catálogos de modelos con fecha), la fecha exacta del host no
forbidden = {str(REAL.year), str(REAL.year + 1), HOST_TODAY.strftime("%Y-%m-%d"), HOST_TODAY.strftime("%d %b %Y")}
check(str(REAL.year + 28) not in forbidden and REAL.year + 28 > HOST_TODAY.year, "el test es ambiguo con este REAL")

from econosim.twins.openrouter import OpenRouterTwin
from econosim.twins.qonto import QontoTwin
from econosim.upstream import FakeUpstream

w, h = make_world(REAL)
o = OpenRouterTwin(w, FakeUpstream(["ok"], 100, 10), api_key="k")
q = QontoTwin(w, secret_key="s")
fn = FakeNet(hang_seconds=1, display_now=lambda: w.clock.display_now)
fn.mount(h.host, h.app())
fn.mount(o.host, o.app())
fn.mount(q.host, q.app())
H = {"Host": h.host, "Authorization": "Bearer test-token"}
HO = {"Host": o.host, "Authorization": "Bearer k"}
HQ = {"Host": q.host, "Authorization": q.auth_header}
seen: list[tuple[str, str]] = []


def leak(text: str) -> str:
    # años como número completo (no dentro de otro número, p.ej. ids), fechas como subcadena
    return next((bad for bad in forbidden if re.search(r"(?<!\d)" + re.escape(bad) + r"(?!\d)", text)), "")


def scan(label: str, text: str) -> None:
    seen.append((label, text))
    found = leak(text)
    check(not found, f"{label}: contiene {found!r}: {text[:300]}")


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

    # OpenRouter: chat (con compra automática), generation, credits, key, modelos
    r = requests.post(U("/api/v1/chat/completions"), headers=HO,
                      json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "x"}], "usage": {"include": True}})
    scan("or-chat", r.text); scan("or-chat-headers", json.dumps(dict(r.headers)))
    gid = r.json()["id"]
    for path in (f"/api/v1/generation?id={gid}", "/api/v1/credits", "/api/v1/auth/key", "/api/v1/models/openai/gpt-4o-mini/endpoints"):
        r = requests.get(U(path), headers=HO)
        scan("or" + path, r.text); scan("or" + path + " headers", json.dumps(dict(r.headers)))
    scan("or-models-headers", json.dumps(dict(requests.get(U("/api/v1/models"), headers=HO).headers)))
    r = requests.post(U("/api/v1/chat/completions"), headers=HO,
                      json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "x"}], "stream": True})
    scan("or-stream", r.text)

    # Qonto: organización y transacciones (incluye la compra de créditos y la factura de Hetzner)
    for path in ("/v2/organization", "/v2/transactions", "/v2/transactions?side=debit"):
        r = requests.get(U(path), headers=HQ)
        scan("qonto" + path, r.text); scan("qonto" + path + " headers", json.dumps(dict(r.headers)))

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
check(leak(json.dumps({"created": REAL.isoformat()})) == str(REAL.year), "el escáner no detecta el año real")
check(leak("Date: " + HOST_TODAY.strftime("%a, %d %b %Y")) == HOST_TODAY.strftime("%d %b %Y"), "el escáner no detecta la fecha del host en cabecera")
check(leak('{"id": 20002, "x": 1999}') == str(REAL.year) and leak('{"id": 19990}') == "", "límites de número")
check(leak(json.dumps(w.state(include_real=True))) == str(REAL.year), "state(include_real) debería filtrar el año real")

check(len(seen) >= 50, f"pocas respuestas escaneadas: {len(seen)}")
check(any(l == "qonto/v2/transactions" and "OpenRouter" in t and "Hetzner" in t for l, t in seen), "las transacciones no muestran ambos cargos")
print("NODATES OK")
