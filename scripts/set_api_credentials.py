#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вписать api_id/api_hash приложения ShapitoGram в BuildVars.java.

Зачем отдельный шаг
-------------------
`TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java` — это то
единственное место, где живёт пара `api_id`/`api_hash`, с которой клиент
регистрируется в MTProto. В репозитории `owpengram/owpengram-android-client`
туда уже вписаны учётные данные XTree Gram (`3893395`), и они рабочие, поэтому
конвейер по умолчанию ничего не меняет.

Но у ShapitoGram должно быть своё приложение: api_id выдаётся на аккаунт под
конкретный проект (my.telegram.org -> API development tools), и по нему Telegram
считает статистику и лимиты. Пока своего нет, шаг берёт значения из секретов и,
если их нет, оставляет upstream-значения и честно об этом сообщает — молчаливая
подмена чужих ключей хуже, чем явное «свой api_id не задан».

Использование
-------------
    set_api_credentials.py <путь-к-BuildVars.java> --id 123456 --hash abcdef...

    set_api_credentials.py <путь-к-BuildVars.java>            # только показать

Без `--id`/`--hash` берутся `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`. Если и их
нет, скрипт печатает, что оставляет как есть, и выходит с кодом 0: это не
ошибка сборки, а решение — собирать на upstream-кредах.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

APP_ID_RE = re.compile(r"^(\s*public\s+static\s+int\s+APP_ID\s*=\s*)(-?\d+)(\s*;)", re.M)
APP_HASH_RE = re.compile(r'^(\s*public\s+static\s+String\s+APP_HASH\s*=\s*")([^"]*)("\s*;)', re.M)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("buildvars", help="путь к BuildVars.java")
    ap.add_argument("--id", default=None, help="api_id (число)")
    ap.add_argument("--hash", default=None, help="api_hash (32 hex-символа)")
    args = ap.parse_args()

    path = pathlib.Path(args.buildvars)
    if not path.is_file():
        print("FAIL: %s не найден" % path)
        return 1

    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    current_id = APP_ID_RE.search(text)
    current_hash = APP_HASH_RE.search(text)
    if not current_id or not current_hash:
        print("FAIL: в %s не нашёл APP_ID / APP_HASH" % path)
        return 1
    print("сейчас в BuildVars.java: api_id=%s, api_hash=%s...%s"
          % (current_id.group(2), current_hash.group(2)[:6], current_hash.group(2)[-4:]))

    api_id = (args.id or os.environ.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (args.hash or os.environ.get("TELEGRAM_API_HASH") or "").strip()

    if not api_id and not api_hash:
        print("свой api_id не задан (TELEGRAM_API_ID / TELEGRAM_API_HASH) — "
              "оставляю учётные данные upstream как есть.")
        print("Это рабочий вариант: они зарегистрированы и принимаются сервером. "
              "Своё приложение можно создать на https://my.telegram.org.")
        return 0

    if not api_id.isdigit():
        print("FAIL: api_id %r не число" % api_id)
        return 1
    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        print("FAIL: api_hash %r не похож на 32 hex-символа" % api_hash)
        return 1

    text = APP_ID_RE.sub(lambda m: m.group(1) + api_id + m.group(3), text, count=1)
    text = APP_HASH_RE.sub(lambda m: m.group(1) + api_hash + m.group(3), text, count=1)

    out = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(out.encode("utf-8"))
    print("OK: api_id=%s, api_hash=%s...%s вписаны в %s"
          % (api_id, api_hash[:6], api_hash[-4:], path.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
