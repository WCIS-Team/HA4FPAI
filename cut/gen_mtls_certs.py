#!/usr/bin/env python3
"""Generate TLS certs for mTLS MQTT: server (IP+DNS SAN) + two client certs.

Runs inside ha-small-dev where the CA from the earlier TLS test already lives
in /tmp/mtls. Outputs:
  /tmp/mtls/server_tls.{key,crt}     broker certificate (SAN: localhost + broker IP)
  /tmp/mtls/client_ha.{key,crt}      HA's client certificate
  /tmp/mtls/client_adapter.{key,crt} board-adapter's client certificate
"""
import subprocess
from pathlib import Path

CA_DIR = Path("/tmp/mtls")
CA_KEY = CA_DIR / "ca.key"
CA_CRT = CA_DIR / "ca.crt"
BROKER_IP = "10.88.0.67"


def run(cmd):
    r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(cmd + "\n" + r.stderr[-400:])
    return r.stdout.strip()


def issue(name, cn, ext_text, server=False):
    key = CA_DIR / (name + ".key")
    csr = CA_DIR / (name + ".csr")
    crt = CA_DIR / (name + ".crt")
    ext = CA_DIR / (name + ".ext")
    ext.write_text(ext_text)
    run(f"openssl genrsa -out {key} 2048")
    run(f"openssl req -new -key {key} -out {csr} -subj '/CN={cn}'")
    run(f"openssl x509 -req -in {csr} -CA {CA_CRT} -CAkey {CA_KEY} -CAcreateserial "
        f"-out {crt} -days 30 -sha256 -extfile {ext}")
    print("%s: %d bytes" % (crt.name, crt.stat().st_size))
    return key, crt


server_ext = """basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:%s""" % BROKER_IP

client_ext = """basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth"""

issue("server_tls", "localhost", server_ext, server=True)
issue("client_ha", "ha-small-ha-client", client_ext)
issue("client_adapter", "board-113-adapter", client_ext)
print("done")

