"""G5: el gemelo Hetzner habla el esquema real y sus precios salen de data/pricing/hetzner.json."""
from __future__ import annotations

import json
from datetime import timedelta

import requests

from _common import ROOT, LiveNet, check, make_world
from econosim.fakenet.server import FakeNet

pricing = json.loads((ROOT / "data" / "pricing" / "hetzner.json").read_text(encoding="utf-8"))
w, h = make_world()
fn = FakeNet(hang_seconds=2, display_now=lambda: w.clock.display_now)
fn.mount(h.host, h.app())
H = {"Host": "api.hetzner.cloud", "Authorization": "Bearer test-token"}

with LiveNet(fn) as net:
    U = net.url

    # --- auth ---------------------------------------------------------------
    r = requests.get(U("/v1/server_types"), headers={"Host": h.host})
    check(r.status_code == 401 and r.json()["error"]["code"] == "unauthorized", "sin token")
    r = requests.get(U("/v1/server_types"), headers={"Host": h.host, "Authorization": "Bearer nope"})
    check(r.status_code == 401, "token malo")

    # --- server_types: esquema y precios == fichero ----------------------------
    r = requests.get(U("/v1/server_types"), headers=H)
    check(r.status_code == 200 and "RateLimit-Limit" in r.headers, "cabeceras")
    body = r.json()
    check(set(body) == {"server_types", "meta"} and body["meta"]["pagination"]["total_entries"] == len(pricing["server_types"]), "meta")
    by_name = {t["name"]: t for t in body["server_types"]}
    for src in pricing["server_types"]:
        t = by_name[src["name"]]
        for k in ("id", "name", "cores", "memory", "disk", "deprecated", "prices", "storage_type", "cpu_type", "architecture"):
            check(k in t, f"falta {k} en server_type")
        check((t["cores"], t["memory"], t["disk"]) == (src["cores"], src["memory"], src["disk"]), src["name"])
        locs = {p["location"] for p in t["prices"]}
        check(locs == {l["name"] for l in pricing["locations"]}, f"ubicaciones de {src['name']}")
        for p in t["prices"]:
            check(float(p["price_monthly"]["net"]) == src["monthly"], f"{src['name']} mensual {p['price_monthly']}")
            check(float(p["price_hourly"]["net"]) == src["hourly"], f"{src['name']} horaria {p['price_hourly']}")
            check(float(p["price_monthly"]["gross"]) > src["monthly"], "bruto <= neto")
            check(p["included_traffic"] == src["traffic_tb"] * 1024 ** 4, "tráfico incluido")
    r = requests.get(U("/v1/server_types/999"), headers=H)
    check(r.status_code == 404 and r.json()["error"]["code"] == "not_found", "tipo inexistente")

    # --- pricing endpoint --------------------------------------------------------
    p = requests.get(U("/v1/pricing"), headers=H).json()["pricing"]
    check(p["currency"] == "EUR", "moneda")
    check(float(p["primary_ips"][0]["prices"][0]["price_monthly"]["net"]) == pricing["primary_ipv4_monthly"], "IPv4")

    # --- catálogo --------------------------------------------------------------------
    check(len(requests.get(U("/v1/locations"), headers=H).json()["locations"]) == len(pricing["locations"]), "locations")
    check(len(requests.get(U("/v1/images"), headers=H).json()["images"]) == len(pricing["images"]), "images")
    dc = requests.get(U("/v1/datacenters"), headers=H).json()
    check("datacenters" in dc and "recommendation" in dc, "datacenters")

    # --- el VPS propio ya existe -----------------------------------------------------
    servers = requests.get(U("/v1/servers"), headers=H).json()["servers"]
    check(len(servers) == 1 and servers[0]["id"] == 1 and servers[0]["status"] == "running", servers)
    s0 = servers[0]
    for k in ("public_net", "server_type", "datacenter", "image", "created", "protection", "labels", "primary_disk_size"):
        check(k in s0, f"falta {k} en server")
    check(s0["public_net"]["ipv4"]["ip"].startswith("5.75.") and s0["server_type"]["name"] == "cx23", s0["public_net"])
    check(s0["created"].startswith("2026-10-14T09:30:00"), s0["created"])

    # --- crear: validación 422, duplicado 409, creación 201 con action running -------
    r = requests.post(U("/v1/servers"), headers=H, json={"name": "x"})
    check(r.status_code == 422 and r.json()["error"]["code"] == "invalid_input", "422 campos")
    names = {f["name"] for f in r.json()["error"]["details"]["fields"]}
    check(names == {"server_type", "image"}, names)
    r = requests.post(U("/v1/servers"), headers=H, json={"name": "x", "server_type": "cx999", "image": "debian-12"})
    check(r.status_code == 422, "tipo desconocido")
    r = requests.post(U("/v1/servers"), headers=H, data="not json")
    check(r.status_code == 422, "body no JSON")
    r = requests.post(U("/v1/servers"), headers=H, json={"name": "vps-1", "server_type": "cx23", "image": "debian-12"})
    check(r.status_code == 409 and r.json()["error"]["code"] == "uniqueness_error", "duplicado")

    r = requests.post(U("/v1/servers"), headers=H,
                      json={"name": "worker", "server_type": "CX33", "image": "ubuntu-24.04", "location": "hel1",
                            "labels": {"role": "worker"}})
    check(r.status_code == 201, r.text)
    body = r.json()
    check(set(body) >= {"server", "action", "next_actions", "root_password"}, body.keys())
    sid = body["server"]["id"]
    check(body["server"]["status"] == "initializing" and body["action"]["status"] == "running", "estados al crear")
    check(body["server"]["datacenter"]["location"]["name"] == "hel1" and body["server"]["labels"] == {"role": "worker"}, "loc/labels")
    aid = body["action"]["id"]
    w.advance(timedelta(seconds=31))
    check(requests.get(U(f"/v1/servers/{sid}"), headers=H).json()["server"]["status"] == "running", "no arrancó")
    check(requests.get(U(f"/v1/actions/{aid}"), headers=H).json()["action"]["status"] == "success", "action no terminó")

    # --- acciones ------------------------------------------------------------------
    r = requests.post(U(f"/v1/servers/{sid}/actions/poweroff"), headers=H)
    check(r.status_code == 201 and r.json()["action"]["command"] == "stop_server", r.text)
    check(requests.get(U(f"/v1/servers/{sid}"), headers=H).json()["server"]["status"] == "off", "poweroff")
    r = requests.post(U(f"/v1/servers/{sid}/actions/poweron"), headers=H)
    check(requests.get(U(f"/v1/servers/{sid}"), headers=H).json()["server"]["status"] == "running", "poweron")
    check(requests.post(U(f"/v1/servers/{sid}/actions/explode"), headers=H).status_code == 404, "acción inexistente")
    acts = requests.get(U(f"/v1/servers/{sid}/actions"), headers=H).json()["actions"]
    check([a["command"] for a in acts] == ["create_server", "stop_server", "start_server"], [a["command"] for a in acts])

    # --- borrar ---------------------------------------------------------------------
    r = requests.delete(U(f"/v1/servers/{sid}"), headers=H)
    check(r.status_code == 200 and r.json()["action"]["command"] == "delete_server", r.text)
    check(requests.get(U(f"/v1/servers/{sid}"), headers=H).status_code == 404, "borrado sigue visible")
    check(requests.delete(U(f"/v1/servers/{sid}"), headers=H).status_code == 404, "doble borrado")
    check(len(requests.get(U("/v1/servers"), headers=H).json()["servers"]) == 1, "lista tras borrar")
    check(w.alive, "murió al borrar un servidor secundario")

    # --- borrar el VPS propio = suicidio ----------------------------------------------
    r = requests.delete(U("/v1/servers/1"), headers=H)
    check(r.status_code == 200, "borrar el propio")
    w.advance(timedelta(seconds=6))
    check(not w.alive and w.episode.death_cause == "self_deleted", "no murió al borrar su VPS")

    # --- cuenta bloqueada: solo lectura ------------------------------------------------
    h.locked = True
    check(requests.get(U("/v1/server_types"), headers=H).status_code == 200, "lectura bloqueada")
    r = requests.post(U("/v1/servers"), headers=H, json={"name": "y", "server_type": "cx23", "image": "debian-12"})
    check(r.status_code == 403, "escritura con cuenta bloqueada")

print("HETZNER OK")
