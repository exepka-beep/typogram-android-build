"""MTProto-рукопожатие снаружи: отвечает ли сервер на req_pq.

Зачем
-----
«Бесконечная загрузка» в клиенте выглядит одинаково и когда сервер молчит, и
когда сервер отвечает не тем, что ждёт клиент, и когда клиент вообще не может
дойти до сервера. Здесь мы говорим с сервером на его языке и проверяем ровно
то, что проверяет клиент: TCP, транспорт, ответ `resPQ` на `req_pq_multi`.

Важная деталь, на которой легко обмануться
------------------------------------------
`req_pq_multi` нельзя послать «голым». Тело запроса обязано лежать в
незашифрованном MTProto-конверте:

    auth_key_id : long   (8 байт, = 0 — сообщение без шифрования)
    msg_id      : long   (8 байт, unixtime << 32 | дробная часть, кратно 4)
    msg_len     : int    (4 байта, длина тела)
    body        : req_pq_multi#be7e8ef1 nonce:int128

Если отправить только 20 байт тела, сервер отвечает 4-байтовой транспортной
ошибкой (`6cfeffff` = -404) — и это легко принять за «сервер сломан», хотя он
просто не разобрал пакет. Поэтому конверт собирается здесь полностью.

Транспорты пробуем оба: сервер поднимает tcp_auto и решает по первым байтам,
а клиент Android по умолчанию ходит intermediate.

resPQ#05162463 nonce:int128 server_nonce:int128 pq:string
"""

import json
import os
import pathlib
import socket
import struct
import sys
import time
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "94.156.170.106"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 2398
TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
SERVER_INFO_URL = (sys.argv[4] if len(sys.argv) > 4
                   else "https://shapitogram.xyz/owpengram/server-info")

REQ_PQ_MULTI = 0xBE7E8EF1
RES_PQ = 0x05162463
ABRIDGED = 0xEF


def msg_id_now():
    """msg_id: старшие 32 бита — unixtime, младшие — доля секунды, кратно 4."""
    now = time.time()
    seconds = int(now)
    fraction = int((now - seconds) * (1 << 32)) & 0xFFFFFFFC
    return (seconds << 32) | fraction


def envelope(body):
    """Незашифрованный MTProto-конверт: то, чего не хватало в первой версии."""
    return struct.pack("<q", 0) + struct.pack("<q", msg_id_now()) \
        + struct.pack("<i", len(body)) + body


def frame(transport, payload):
    if transport == "abridged":
        words = len(payload) // 4
        if words < 0x7F:
            return bytes([ABRIDGED, words]) + payload
        return bytes([ABRIDGED, 0x7F]) + words.to_bytes(3, "little") + payload
    if transport == "intermediate":
        return b"\xee\xee\xee\xee" + struct.pack("<i", len(payload)) + payload
    raise ValueError(transport)


def recv_exactly(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            break
        data += chunk
    return data


def read_frame(sock, transport):
    """Возвращает (payload, ошибка). payload — уже без транспортного заголовка."""
    if transport == "intermediate":
        header = recv_exactly(sock, 4)
        if len(header) < 4:
            return None, "сервер закрыл соединение, не отдав заголовок"
        length = struct.unpack("<i", header)[0]
    else:
        first = recv_exactly(sock, 1)
        if not first:
            return None, "сервер закрыл соединение"
        head = first[0]
        # В abridged ответы идут без маркера 0xEF: первый байт — уже длина в
        # 4-байтовых словах. 0xEF клиент шлёт один раз, выбирая транспорт.
        # 0x7F — escape для длин от 127 слов, 0xFF — транспортная ошибка
        # (4 байта LE), и она однозначна: длина 255 слов кодируется как 0x7F.
        if head == 0x7F:
            length = int.from_bytes(recv_exactly(sock, 3), "little") * 4
        elif head == 0xFF:
            raw = first + recv_exactly(sock, 3)
            return None, "транспортная ошибка сервера (%d)" \
                % int.from_bytes(raw, "little", signed=True)
        else:
            length = head * 4

    if length < 0:
        # Транспортная ошибка MTProto: 4 байта LE, -1 = «повтори другим
        # транспортом», -404 = пакет не разобран.
        return None, "транспортная ошибка сервера (%d)" % length
    if length == 0:
        return None, "нулевая длина"
    if length > 1 << 20:
        return None, "неправдоподобная длина %d" % length
    payload = recv_exactly(sock, length)
    if len(payload) < length:
        return None, "пришло %d байт из %d" % (len(payload), length)
    return payload, None


def attempt(transport):
    nonce = os.urandom(16)
    body = struct.pack("<I", REQ_PQ_MULTI) + nonce
    print("\n--- транспорт: %s ---" % transport)
    started = time.time()
    sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
    try:
        print("  TCP установлен за %.0f мс" % ((time.time() - started) * 1000))
        sock.sendall(frame(transport, envelope(body)))
        sock.settimeout(TIMEOUT)

        payload, error = read_frame(sock, transport)
        if error:
            print("  %s" % error)
            return None
        print("  ответ %d байт за %.0f мс" % (len(payload), (time.time() - started) * 1000))

        # Разбираем конверт ответа. Незашифрованный ответ тоже начинается с
        # auth_key_id = 0, поэтому тело — с 21-го байта.
        if len(payload) < 24:
            print("  слишком короткий конверт: %s" % payload.hex())
            return None
        auth_key_id, reply_id, reply_len = struct.unpack("<qqi", payload[:20])
        inner = payload[20:20 + reply_len] if 0 < reply_len <= len(payload) - 20 else payload[20:]
        print("  auth_key_id=%d, тело %d байт" % (auth_key_id, len(inner)))

        if len(inner) < 4:
            print("  тело короче конструктора: %s" % inner.hex())
            return None
        constructor = struct.unpack("<I", inner[:4])[0]
        print("  конструктор: 0x%08x" % constructor)
        print("  тело: %s" % inner.hex())

        if constructor == RES_PQ:
            echoed = "совпал" if inner[4:20] == nonce else "НЕ совпал"
            print("  эхо nonce: %s" % echoed)
            if echoed != "совпал":
                return None
            # resPQ#05162463 nonce:int128 server_nonce:int128 pq:string
            #               server_public_key_fingerprints:Vector<long>
            #
            # Две ловушки, обе стоили времени:
            #  1) server_nonce тоже 16 байт — без него pq разбирается со сдвигом;
            #  2) байтовый массив в MTProto добивается до 4-байтовой границы
            #     (NativeByteBuffer::readByteArray: addition = (l + sl) % 4).
            #     pq из 8 байт занимает 12, а не 9, — отсюда «лишние» три нуля.
            # server_cookies в этой схеме нет: клиентский TL_resPQ::readParams
            # читает только fingerprint'ы (MTProtoScheme.cpp:191).
            cursor = 36
            pq_len = inner[cursor]
            prefix = 1
            if pq_len >= 254:
                pq_len = int.from_bytes(inner[cursor + 1:cursor + 4], "little")
                prefix = 4
            total = prefix + pq_len
            total += (-total) % 4          # добивка до 4 байт
            pq = inner[cursor + prefix:cursor + prefix + pq_len]
            cursor += total
            print("  pq (%d байт): %s" % (len(pq), pq.hex()))

            magic = int.from_bytes(inner[cursor:cursor + 4], "little")
            if magic != 0x1cb5c415:
                print("  ожидал vector#1cb5c415, а тут 0x%08x" % magic)
                return None
            cursor += 4
            count = int.from_bytes(inner[cursor:cursor + 4], "little")
            cursor += 4
            fingerprints = []
            for _ in range(min(count, 16)):
                fingerprints.append(int.from_bytes(inner[cursor:cursor + 8], "little"))
                cursor += 8
            print("  отпечатки ключей сервера (%d):" % len(fingerprints))
            for value in fingerprints:
                print("    0x%016x  (%d)" % (value, value))
            return fingerprints

        print("  не resPQ: %s" % inner[:48].hex())
        return None
    finally:
        sock.close()


print("=== %s:%d ===" % (HOST, PORT))
results = {}
for transport in ("intermediate", "abridged"):
    try:
        results[transport] = attempt(transport)
    except Exception as error:
        print("  ошибка: %s: %s" % (type(error).__name__, error))
        results[transport] = None

good = [t for t, value in results.items() if value]
if not good:
    print()
    print("Сервер не ответил resPQ ни по одному транспорту.")
    raise SystemExit(1)

print()
print("ОК: сервер отвечает resPQ по транспорту %s" % ", ".join(good))

# Финальная сверка: сервер в resPQ перечисляет отпечатки ключей, которыми
# готов подписываться. Если среди них нет ключа, который клиент берёт из
# /owpengram/server-info, рукопожатие дойдёт до req_DH_params и оборвётся —
# снаружи это неотличимо от «сервер молчит». Поэтому сверяем здесь.
fingerprints = results[good[0]]
try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    from fetch_server_key import fingerprint  # noqa: E402

    info = json.loads(urllib.request.urlopen(SERVER_INFO_URL, timeout=TIMEOUT)
                      .read().decode("utf-8"))
    pem = info.get("rsa_public_key_pem", "")
    ours = fingerprint(pem)
    print()
    print("ключ из %s:" % SERVER_INFO_URL)
    print("  отпечаток 0x%016x  (%d)" % (ours, ours))
    if ours in fingerprints:
        print("  ✅ сервер его принимает — клиент пройдёт рукопожатие")
        raise SystemExit(0)
    print("  ❌ сервер его НЕ принимает. Клиент дойдёт до req_DH_params и оборвётся.")
    raise SystemExit(1)
except SystemExit:
    raise
except Exception as error:
    print()
    print("не смог сверить ключ (%s: %s) — рукопожатие всё равно началось"
          % (type(error).__name__, error))
    raise SystemExit(0)
