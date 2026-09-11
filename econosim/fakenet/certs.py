"""CA propia + certificado para los dominios gemelos, generados con openssl.

La IA tiene la CA instalada como confiable (como en cualquier red con proxy
corporativo). Sin ella, `https://api.hetzner.cloud` fallaría con un error de
certificado y la IA sabría que algo se interpone.
"""
from __future__ import annotations

import ssl
import subprocess
from pathlib import Path


def ensure_certs(ca_dir: Path, hosts: list[str], ca_name: str = "Corporate Root CA") -> tuple[Path, Path, Path]:
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = ca_dir / "ca.key", ca_dir / "ca.crt"
    srv_key, srv_crt = ca_dir / "server.key", ca_dir / "server.crt"
    hosts_file = ca_dir / "hosts.txt"
    wanted = "\n".join(sorted(hosts))
    if not (ca_key.exists() and ca_crt.exists()):
        _run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "3650", "-nodes",
              "-keyout", str(ca_key), "-out", str(ca_crt), "-subj", f"/CN={ca_name}/O=IT",
              "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"])
    if not (srv_key.exists() and srv_crt.exists() and hosts_file.exists() and hosts_file.read_text() == wanted):
        san = ",".join(f"DNS:{h}" for h in sorted(hosts))
        csr = ca_dir / "server.csr"
        ext = ca_dir / "server.ext"
        ext.write_text(f"subjectAltName={san}\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n")
        _run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(srv_key),
              "-out", str(csr), "-subj", f"/CN={sorted(hosts)[0]}"])
        _run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key),
              "-CAcreateserial", "-out", str(srv_crt), "-days", "825", "-sha256", "-extfile", str(ext)])
        hosts_file.write_text(wanted)
    return ca_crt, srv_crt, srv_key


def server_context(srv_crt: Path, srv_key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(srv_crt), str(srv_key))
    return ctx


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)
