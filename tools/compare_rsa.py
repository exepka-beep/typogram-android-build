"""Сверка RSA-ключей: что вшито в APK против того, что отдаёт сервер.

Зачем отдельный скрипт
----------------------
В APK лежат ДВА ключа: наш (OWPENGRAM_RSA_KEY) и официальный Telegram
(TELEGRAM_RSA_KEY). Поиск «первого попавшегося» даёт ключ Telegram и уводит
не туда.

Сравниваем не отпечатки, а само тело PEM: отпечаток MTProto легко посчитать
неправильно (лишний байт конструктора, другой порядок байт в хвосте SHA-1), а
тело ключа — это ровно то, что уходит в рукопожатие. Если тела совпали
побайтово, ключ верный; всё остальное — производные.

Отпечаток считаем как в MTProto: SHA-1 от TL-сериализации `int256 n` + `int e`
(знаковые, big-endian, минимальная длина, без id конструктора), берём
последние 8 байт.
"""

import base64
import hashlib
import json
import pathlib
import re
import struct
import sys
import urllib.request
import zipfile


def pem_body(pem_text):
    return "".join(line for line in pem_text.splitlines()
                   if line and not line.startswith("-----"))


def fingerprint_from_body(body):
    der = base64.b64decode(body)
    i = 1
    i += (1 + (der[i] & 0x7F)) if der[i] & 0x80 else 1
    if der[i] == 0x30:                      # SPKI: заходим в AlgorithmIdentifier
        i += 1
        i += (1 + (der[i] & 0x7F)) if der[i] & 0x80 else 1
        if der[i] == 0x02:                  # это был PKCS#1 — откатываемся
            i = 1
            i += (1 + (der[i] & 0x7F)) if der[i] & 0x80 else 1

    def read_int():
        nonlocal i
        i += 1
        length = der[i]
        i += 1
        if length & 0x80:
            count = length & 0x7F
            length = int.from_bytes(der[i:i + count], "big")
            i += count
        value = der[i:i + length]
        i += length
        return int.from_bytes(value, "big")

    n, e = read_int(), read_int()
    n_len = (n.bit_length() + 8) // 8
    e_len = (e.bit_length() + 8) // 8
    payload = n.to_bytes(n_len, "big") + e.to_bytes(e_len, "big")
    digest = hashlib.sha1(payload).digest()
    return struct.unpack("<q", digest[-8:])[0]


def keys_from_apk(path):
    """Все PEM-ключи, вшитые в dex, в порядке появления."""
    with zipfile.ZipFile(path) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist()
                        if name.startswith("classes") and name.endswith(".dex"))
    text = blob.decode("latin-1")
    # В Java-строке "\n" — это НАСТОЯЩИЙ перевод строки, поэтому в regex \n,
    # а не литерал из обратного слэша и буквы n.
    pattern = re.compile(
        r"-----BEGIN RSA PUBLIC KEY-----\n(.+?)\n-----END RSA PUBLIC KEY-----", re.S)
    return [(match.start(), pem_body(match.group(1))) for match in pattern.finditer(text)]


def main():
    apk = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                       "dist/apk/ShapitoGram-12.10.1-build5.apk")
    info_url = sys.argv[2] if len(sys.argv) > 2 else \
        "https://shapitogram.xyz/owpengram/server-info"

    print("=== сервер ===")
    with urllib.request.urlopen(info_url, timeout=30) as response:
        info = json.loads(response.read().decode())
    server_body = pem_body((info.get("rsa_public_key_pem") or "").strip())
    print("  name       : %s" % info.get("name"))
    print("  dc_id      : %s" % info.get("dc_id"))
    print("  тело ключа : %d символов, %s..." % (len(server_body), server_body[:40]))
    print("  отпечаток  : %d" % fingerprint_from_body(server_body))

    print("\n=== ключи в APK ===")
    keys = keys_from_apk(apk)
    if not keys:
        raise SystemExit("в APK не нашлось ни одного PEM-ключа")

    matched = False
    for offset, body in keys:
        same = body == server_body
        if same:
            matched = True
        print("  позиция %-9d %-18s тело %d символов, %s..."
              % (offset, "НАШ КЛЮЧ" if same else "не наш", len(body), body[:40]))
        if not same:
            print("      отпечаток: %d" % fingerprint_from_body(body))

    print()
    if matched:
        print("СОВПАДЕНИЕ: наш ключ в APK побайтово равен ключу сервера.")
        return 0
    print("РАСХОЖДЕНИЕ: ключа сервера в APK нет.")
    print("Рукопожатие не пройдёт — приложение будет бесконечно грузиться.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
