"""Verify the mTLS policy of the lab broker: no cert rejected, valid cert accepted."""
import socket
import ssl
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else '10.88.0.67'
CA = sys.argv[2] if len(sys.argv) > 2 else '/tmp/mtls/ca.crt'
CERT = sys.argv[3] if len(sys.argv) > 3 else '/tmp/mtls/client_ha.crt'
KEY = sys.argv[4] if len(sys.argv) > 4 else '/tmp/mtls/client_ha.key'
PORT = 8883


def attempt(label, with_client_cert):
    ctx = ssl.create_default_context(cafile=CA)
    if with_client_cert:
        ctx.load_cert_chain(CERT, KEY)
    try:
        raw = socket.create_connection((HOST, PORT), timeout=8)
        tls = ctx.wrap_socket(raw, server_hostname=HOST)
        version = tls.version()
        cipher = tls.cipher()[0] if tls.cipher() else '?'
        # CONNECT still has to succeed for a truly usable session
        tls.sendall(b'\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00')
        reply = tls.recv(4)
        tls.close()
        ok = len(reply) >= 2 and reply[0] == 0x20
        print('%-28s handshake=%s cipher=%s mqtt_connack=%s -> %s'
              % (label, version, cipher, reply.hex() or 'none',
                 'ACCEPTED' if ok else 'handshake ok but CONNACK refused'))
        return ok
    except ssl.SSLError as exc:
        print('%-28s -> REJECTED by TLS: %s' % (label, str(exc)[:90]))
        return False
    except Exception as exc:  # noqa: BLE001
        print('%-28s -> FAILED: %s: %s' % (label, type(exc).__name__, str(exc)[:70]))
        return False


no_cert = attempt('without client cert', False)
with_cert = attempt('with client cert', True)

print()
print('no-cert accepted   :', no_cert, '(expected False)')
print('with-cert accepted :', with_cert, '(expected True)')
ok = (not no_cert) and with_cert
print('mTLS policy        :', 'ENFORCED' if ok else 'NOT ENFORCED')
raise SystemExit(0 if ok else 1)
