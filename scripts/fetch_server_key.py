#!/usr/bin/env python3
"""Download the MTProto public key of the ShapitoGram server.

Why this exists
---------------
The server runs with ``TELESRV_RSA_IDENTITY_MODE=generated``: on the very first
start it creates its own RSA keypair and keeps it in the ``server_state`` volume
at ``/var/lib/telesrv/server_rsa.pem``. Nothing in this repository can know that
key in advance, so the value baked into the iOS client has to be fetched from
the running server — otherwise every build ships a stale key, the handshake
fails and the app hangs on the launch screen.

The server exposes exactly what we need at ``/owpengram/server-info``:

    {"rsa_public_key_pem": "...", "dc_id": 2, "name": "...", ...}

Usage
-----
    fetch_server_key.py --url https://example.com/owpengram/server-info --out config/server-rsa-public.pem
    fetch_server_key.py --domain example.com --out config/server-rsa-public.pem

``--insecure`` skips certificate verification, which is what you need while the
server is still answering on a self-signed cert before certbot has run. Do not
use it for a release build.

Exit code is non-zero on any failure so the CI step stops instead of compiling a
client that cannot authenticate.
"""

import argparse
import base64
import hashlib
import json
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request

SERVER_INFO_PATH = "/owpengram/server-info"
BEGIN_RE = re.compile(r"^-----BEGIN [A-Z0-9 ]*PUBLIC KEY-----\s*$", re.M)


def _tl_bytes(value):
    """TL-serialize a byte string (length prefix + 4-byte alignment)."""
    if len(value) < 254:
        head = bytes([len(value)])
    else:
        head = b"\xfe" + len(value).to_bytes(3, "little")
    body = head + value
    return body + b"\x00" * ((4 - len(body) % 4) % 4)


def _der_ints(der):
    """PKCS#1 RSAPublicKey -> (n, e) as minimal big-endian bytes."""
    if der[0] != 0x30:
        raise ValueError("not an ASN.1 SEQUENCE")
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    out = []
    for _ in range(2):
        if der[i] != 0x02:
            raise ValueError("expected an ASN.1 INTEGER")
        i += 1
        length = der[i]
        i += 1
        if length & 0x80:
            count = length & 0x7F
            length = int.from_bytes(der[i:i + count], "big")
            i += count
        value = der[i:i + length]
        i += length
        while len(value) > 1 and value[0] == 0:
            value = value[1:]
        out.append(value)
    return out[0], out[1]


def _to_pkcs1(der):
    """Accept PKCS#1 or SubjectPublicKeyInfo; return the PKCS#1 DER blob."""
    for offset in range(len(der)):
        if der[offset] != 0x30:
            continue
        try:
            n, e = _der_ints(der[offset:])
        except Exception:
            continue
        if len(n) == 256 and e == b"\x01\x00\x01":
            header = 2 + (der[offset + 1] & 0x7F) if der[offset + 1] & 0x80 else 2
            return der[offset:offset + header + int.from_bytes(der[offset + 2:offset + header], "big")]
    raise ValueError("no 2048-bit RSA public key found in the PEM")


def fingerprint(pem_text):
    """MTProto RSA fingerprint: SHA1 over TL(n) || TL(e), bytes 12..20 LE."""
    body = "".join(re.findall(r"^(?!-----)([A-Za-z0-9+/=]+)\s*$", pem_text, re.M))
    if not body:
        raise ValueError("no base64 payload in the PEM")
    der = _to_pkcs1(base64.b64decode(body))
    n, e = _der_ints(der)
    digest = hashlib.sha1(_tl_bytes(n) + _tl_bytes(e)).digest()
    return int.from_bytes(digest[12:20], "little")


def build_url(args):
    if args.url:
        return args.url
    if args.domain:
        domain = args.domain.strip().rstrip("/")
        if not domain.startswith(("http://", "https://")):
            domain = "https://" + domain
        return domain + SERVER_INFO_PATH
    raise SystemExit("укажи --url или --domain")


def fetch(url, insecure, timeout):
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    request = urllib.request.Request(url, headers={
        "User-Agent": "shapitogram-ios-pipeline/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit("HTTP %s на %s — эндпоинт есть? nginx проксирует /owpengram/?"
                         % (error.code, url))
    except urllib.error.URLError as error:
        raise SystemExit("не достучались до %s: %s" % (url, error.reason))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="полный URL /owpengram/server-info")
    parser.add_argument("--domain", help="домен сервера; путь добавится сам")
    parser.add_argument("--out", required=True, help="куда записать публичный ключ")
    parser.add_argument("--insecure", action="store_true",
                        help="не проверять TLS-сертификат (до certbot)")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    url = build_url(args)
    print("server-info: %s" % url)
    payload = fetch(url, args.insecure, args.timeout)

    try:
        data = json.loads(payload.decode("utf-8"))
    except ValueError:
        raise SystemExit("ответ не JSON — вероятно, отдал nginx или другой сервис: %r"
                         % payload[:200])

    pem = (data.get("rsa_public_key_pem") or "").strip()
    if not BEGIN_RE.search(pem):
        raise SystemExit("в ответе нет rsa_public_key_pem. Ключи: %s"
                         % ", ".join(sorted(data)) if isinstance(data, dict) else "?")

    pem = pem.rstrip() + "\n"
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(pem)

    print("сервер: %s (dc_id=%s)" % (data.get("name", "?"), data.get("dc_id", "?")))
    print("fingerprint: 0x%016x" % fingerprint(pem))
    print("ключ записан: %s (%d байт)" % (out, len(pem)))


if __name__ == "__main__":
    main()
