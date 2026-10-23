#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ребрендинг Android-клиента под ShapitoGram — шаг конвейера, а не разовая правка.

Чем этот скрипт отличается от `rebrand/rebrand_android.py`
----------------------------------------------------------
Тот писался под ЧИСТЫЙ upstream: в нём есть правило `Telegram -> ShapitoGram`, и
больше ничего не нужно, потому что upstream весь текст пишет словом Telegram.

Android-конвейер ShapitoGram работает иначе: он накладывает overlay из 205 файлов
XTree Gram (см. `tools/make_overlay.py`). В overlay текст уже переписан на
`Xtreegram`/`XTree Gram` — 1080 вхождений в 10 локалях strings.xml и 62 в 24
Java-файлах. Правило `Telegram -> ShapitoGram` такие строки НЕ ловит: в
`Xtreegram` нет подстроки `Telegram` (там `treegram`, а не `Telegram`). Поэтому
здесь две группы правил, и первая — снятие следов XTree Gram.

Почему в Java применяется только группа XTree Gram
--------------------------------------------------
В Java правило `Telegram -> ShapitoGram` опасно: слово встречается в именах
(`TelegramRsaKey`), в путях (`org/telegram/...`) и в ссылках (`t.me/Telegram`).
Правила XTree Gram такого риска не несут — проверено по overlay: `Xtreegram` в
Java ни разу не стоит в идентификаторе, только в комментариях и в текстовых
литералах (`"XTree Gram"`, `t.me/XtreegramTips`). Единственные
`Xtreegram`-в-идентификаторе входа — это `APP_PACKAGE` и `RELEASE_KEY_*` в
gradle.properties, и они правятся отдельными регекспами ниже.

Что правится
------------
  1. gradle.properties      — APP_PACKAGE, APP_VERSION_CODE/NAME, RELEASE_KEY_*
  2. values*/strings.xml    — AppName/AppNameBeta + оба набора правил по тексту
  3. 24 Java-файла overlay  — только правила XTree Gram (комментарии + литералы)
  4. OwpengramServers.java  — имя, описание, DEFAULT_HOST/PORT, OWPENGRAM_RSA_KEY

Запуск
------
    python3 rebrand_android.py <путь-к-TMessagesProj> \
        --host 94.156.170.106 --port 2398 --rsa-pem config/server-rsa-public.pem \
        --version-code 7042 --version-name 12.10.1 \
        --keystore-alias shapitogramkey --keystore-password ShapitoStore2026

    python3 rebrand_android.py <путь-к-TMessagesProj> --check     # только проверить

`--host`, `--port` и `--rsa-pem` обязательны: в overlay лежат адрес и ключ
сервера XTree Gram (`2.27.200.203`), и без замены клиент уйдёт на чужой сервер.

🔴 `--host` принимает **только числовой IPv4**. Нативный слой Android зовёт
`inet_pton` (ConnectionSocket.cpp, ветка прямого подключения) и имён не
понимает: на домене сокет закрывается сразу, клиент переподключается по кругу,
на экране — вечная загрузка без единой ошибки в логе. Проверка стоит здесь,
чтобы домен не мог попасть в APK даже случайно.

`--rsa-pem` проверяется на формат PEM до подстановки — файл-заглушка здесь
опаснее отсутствия файла, потому что сборка с ней проходит.

`--check` не меняет ничего: он ищет остатки XTree Gram в дереве и падает, если
находит. Это тот же контроль, что и в конце обычного прогона, но применимый
отдельным шагом (например, после overlay, до сборки).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent

# brand.json лежит рядом с конвейером (android/brand.json), а не рядом со скриптом.
# В репозитории сборки `scripts/` и `brand.json` — соседи, локально — тоже.
BRAND_CANDIDATES = (HERE.parent / "brand.json", HERE / "brand.json")
for _candidate in BRAND_CANDIDATES:
    if _candidate.is_file():
        BRAND = json.loads(_candidate.read_text(encoding="utf-8"))
        BRAND_PATH = _candidate
        break
else:
    raise SystemExit("не нашёл brand.json рядом с %s" % HERE)

# Отпечаток ключа считаем тем же кодом, что и в fetch_server_key.py: два
# независимых вычисления одного числа — это уже проверка, а не дублирование.
sys.path.insert(0, str(HERE))
try:
    from fetch_server_key import fingerprint as rsa_fingerprint
except ImportError:  # pragma: no cover - скрипт всегда лежит рядом
    rsa_fingerprint = None

# Правила снятия следов XTree Gram. Именно они применяются к Java; группа
# Telegram — только к strings.xml, где текст не может быть идентификатором.
XTREE_SOURCES = ("XTree Gram", "Xtreegram", "XTREEGRAM")

# Файлы, которые надо просмотреть в поисках следов XTree Gram.
JAVA_MARKERS = ("XTree Gram", "Xtreegram", "XTREEGRAM")

PEM_BEGIN = re.compile(r"^-----BEGIN [A-Z0-9 ]*PUBLIC KEY-----\s*$", re.M)
PEM_END = re.compile(r"^-----END [A-Z0-9 ]*PUBLIC KEY-----\s*$", re.M)



def xtree_rules():
    return [r for r in BRAND["replace"]["rules"] if r["from"] in XTREE_SOURCES]


def all_rules():
    return list(BRAND["replace"]["rules"])


def read_crlf(p: pathlib.Path):
    """(текст с нормализованными \\n, был ли CRLF, исходные байты).

    Перевод строки нормализуется, потому что overlay собран на Windows и часть
    текстовых файлов там CRLF, а в репозитории сборки всё LF. Если писать
    результат в исходной кодировке, дерево останется сборным, но перестанет
    совпадать с тем, что лежит в git, и `git status` в CI будет врать.
    """
    raw = p.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")
    return text, crlf, raw


def write_text(p: pathlib.Path, text: str, crlf: bool, raw: bytes, backup: bool):
    if backup:
        p.with_suffix(p.suffix + ".bak-shapito").write_bytes(raw)
    out = text.replace("\n", "\r\n") if crlf else text
    p.write_bytes(out.encode("utf-8"))


def rebrand_text_nodes(text: str, rules) -> str:
    """Замена только в текстовых узлах XML (не в именах тегов и атрибутах).

    Разбиваем по тегам и правим исключительно то, что между ними. Иначе
    `<string name="TelegramChats">` превратился бы в `<string name="ShapitoGramChats">`
    и сборка упала бы на ссылках на несуществующий ресурс.
    """
    parts = re.split(r"(<[^>]*>)", text)
    out = []
    for token in parts:
        if token.startswith("<") and token.endswith(">"):
            out.append(token)
            continue
        for rule in rules:
            src, dst = rule["from"], rule["to"]
            skip_after = tuple(rule.get("skip_after", []))
            skip_before = tuple(rule.get("skip_before", []))
            idx = 0
            while True:
                j = token.find(src, idx)
                if j < 0:
                    break
                nxt = token[j + len(src): j + len(src) + 4]
                prev = token[j - 1] if j > 0 else ""
                if nxt in skip_after or prev in skip_before:
                    idx = j + len(src)
                    continue
                token = token[:j] + dst + token[j + len(src):]
                idx = j + len(dst)
        out.append(token)
    return "".join(out)


def patch_gradle_properties(root: pathlib.Path, args, report: list) -> None:
    """APP_PACKAGE, версия и ключ подписи. Всё — точными регекспами.

    Именно этот файл делает ShapitoGram отдельным приложением, а не обновлением
    XTree Gram: `defaultConfig.applicationId = APP_PACKAGE` в
    TMessagesProj_App/build.gradle. Разный applicationId — разные приложения,
    поэтому обе сборки могут стоять на телефоне одновременно.
    """
    a = BRAND["android"]
    gp = root.parent / "gradle.properties"
    if not gp.is_file():
        report.append("FAIL gradle.properties не найден (%s)" % gp)
        return

    text, crlf, raw = read_crlf(gp)
    original = text
    changed = []

    def sub(pattern, replacement, label):
        nonlocal text
        text, n = re.subn(pattern, replacement, text, flags=re.M)
        changed.append("%s=%s" % (label, n))
        return n

    sub(r"^APP_PACKAGE=.*$", "APP_PACKAGE=%s" % a["package"], "package")
    if args.version_code:
        sub(r"^APP_VERSION_CODE=.*$", "APP_VERSION_CODE=%s" % args.version_code, "versionCode")
    if args.version_name:
        sub(r"^APP_VERSION_NAME=.*$", "APP_VERSION_NAME=%s" % args.version_name, "versionName")
    alias = args.keystore_alias or os.environ.get("ANDROID_KEY_ALIAS") or a["keystore_alias"]
    # Пароль ищем в трёх местах, потому что в brand.json его нет: файл уезжает в
    # публичный репозиторий. Пустое значение — это отказ, а не «оставим как есть»:
    # gradle.properties в overlay содержит заглушку, и сборка упала бы на подписи
    # с невнятным сообщением gradle вместо понятного здесь.
    password = (args.keystore_password
                or os.environ.get("ANDROID_KEYSTORE_PASSWORD")
                or a.get("keystore_password"))
    if not password:
        report.append("FAIL пароль ключа подписи не задан: --keystore-password, "
                      "$ANDROID_KEYSTORE_PASSWORD или секрет ANDROID_KEYSTORE_PASSWORD")
        return
    sub(r"^RELEASE_KEY_ALIAS=.*$", "RELEASE_KEY_ALIAS=%s" % alias, "keyAlias")
    sub(r"^RELEASE_STORE_PASSWORD=.*$", "RELEASE_STORE_PASSWORD=%s" % password, "storePassword")
    sub(r"^RELEASE_KEY_PASSWORD=.*$", "RELEASE_KEY_PASSWORD=%s" % password, "keyPassword")

    if text == original:
        report.append("FAIL gradle.properties: ни одна строка не изменилась (%s)"
                      % ", ".join(changed))
        return
    write_text(gp, text, crlf, raw, args.backup)
    report.append("OK   gradle.properties: %s" % ", ".join(changed))


def patch_strings(root: pathlib.Path, args, report: list) -> None:
    a = BRAND["android"]
    res = root / "src" / "main" / "res"
    files = sorted(res.glob("values*/strings.xml"))
    if not files:
        report.append("FAIL values*/strings.xml не найдены в %s" % res)
        return

    app_name_hits = 0
    text_hits = 0
    changed_files = 0
    for path in files:
        text, crlf, raw = read_crlf(path)
        original = text
        text, n1 = re.subn(r'(<string\s+name="AppName">)[^<]*(</string>)',
                           rf'\g<1>{a["app_name"]}\g<2>', text)
        text, n2 = re.subn(r'(<string\s+name="AppNameBeta">)[^<]*(</string>)',
                           rf'\g<1>{a["app_name_beta"]}\g<2>', text)
        app_name_hits += n1 + n2
        before = text
        text = rebrand_text_nodes(text, all_rules())
        if text != before:
            text_hits += 1
        if text != original:
            write_text(path, text, crlf, raw, args.backup)
            changed_files += 1
    report.append("OK   strings.xml: AppName/AppNameBeta %d, текстовых файлов изменено %d из %d"
                  % (app_name_hits, changed_files, len(files)))
    if app_name_hits != 2 * len(files):
        report.append("FAIL strings.xml: AppName найден не во всех локалях (%d из %d)"
                      % (app_name_hits, 2 * len(files)))


def patch_java(root: pathlib.Path, args, report: list) -> None:
    """Следы XTree Gram в Java: комментарии и текстовые литералы.

    Список файлов не задаётся руками — он ищется по содержимому. Так шаг
    остаётся верным и если overlay когда-нибудь пополнится новым файлом.
    """
    rules = xtree_rules()
    java_root = root / "src" / "main" / "java"
    if not java_root.is_dir():
        report.append("FAIL нет каталога %s" % java_root)
        return

    touched = 0
    hits = 0
    for path in sorted(java_root.rglob("*.java")):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.append("FAIL %s: не UTF-8" % path.relative_to(root))
            continue
        if not any(m in text for m in JAVA_MARKERS):
            continue
        original = text
        for rule in rules:
            hits += text.count(rule["from"])
            text = text.replace(rule["from"], rule["to"])
        if text != original:
            crlf = b"\r\n" in raw
            write_text(path, text.replace("\r\n", "\n") if crlf else text, crlf, raw, args.backup)
            touched += 1
    report.append("OK   Java: следов XTree Gram %d, файлов изменено %d" % (hits, touched))


def patch_servers(root: pathlib.Path, args, report: list) -> None:
    """OwpengramServers.java — адрес, порт, ключ и подпись встроенного сервера.

    DEFAULT_HOST в overlay — это `2.27.200.203`, старый VPS XTree Gram. Если
    забыть его переписать, клиент уйдёт на чужой сервер и логин не пройдёт;
    поэтому здесь проверяется, что замена реально произошла.
    """
    pc = BRAND["pc_client"]
    path = (root / "src" / "main" / "java" / "org" / "telegram" / "owpengram"
            / "OwpengramServers.java")
    if not path.is_file():
        report.append("FAIL OwpengramServers.java не найден")
        return

    text, crlf, raw = read_crlf(path)
    original = text
    counts = {}

    def sub(pattern, replacement, label, count=1):
        nonlocal text
        text, n = re.subn(pattern, replacement, text, count=count)
        counts[label] = n
        return n

    sub(r'(s\.name\s*=\s*)"[^"]*"', rf'\g<1>"{BRAND["product"]["name"]}"', "name")
    sub(r'(s\.description\s*=\s*)"[^"]*"',
        rf'\g<1>"{pc["server_description"]}"', "description")

    if not args.host:
        report.append("FAIL OwpengramServers.java: не задан --host")
        return
    if not sub(r'(DEFAULT_HOST\s*=\s*)"[^"]*"', rf'\g<1>"{args.host}"', "host"):
        report.append("FAIL OwpengramServers.java: DEFAULT_HOST не найден")
        return
    if not sub(r"(DEFAULT_PORT\s*=\s*)\d+", rf"\g<1>{args.port}", "port"):
        report.append("FAIL OwpengramServers.java: DEFAULT_PORT не найден")
        return

    if args.rsa_pem:
        pem_path = pathlib.Path(args.rsa_pem)
        if not pem_path.is_file():
            report.append("FAIL --rsa-pem %s не найден" % pem_path)
            return
        pem = pem_path.read_text(encoding="utf-8").strip()
        # Проверка до подстановки, а не после: без неё файл-заглушка (в iOS-конвейере
        # config/server-rsa-public.pem — это одни комментарии) превращается в
        # строковый литерал Java, сборка проходит, а приложение не может
        # договориться с сервером. Симптом — вечный сплэш, и искать причину
        # придётся в собранном APK, а не здесь.
        if not PEM_BEGIN.search(pem) or not PEM_END.search(pem):
            report.append("FAIL --rsa-pem %s: это не PEM с публичным ключом "
                          "(нет BEGIN/END ... PUBLIC KEY)" % pem_path)
            return
        body = "\\n".join(pem.splitlines())
        # OWPENGRAM_RSA_FINGERPRINT остаётся 0: нативный слой считает отпечаток из
        # PEM сам (ConnectionsManager::applyServerConfig). Вписывать сюда число не
        # нужно и опасно — разойдётся с ключом при его перегенерации на сервере.
        pattern = re.compile(r"(static final String OWPENGRAM_RSA_KEY\s*=\s*)(.*?)(;\n)", re.S)
        text, n = pattern.subn(lambda m: m.group(1) + '"' + body + '"' + m.group(3),
                               text, count=1)
        counts["rsa"] = n
        if not n:
            report.append("FAIL OwpengramServers.java: OWPENGRAM_RSA_KEY не найден")
            return
        if rsa_fingerprint is not None:
            # То же число сервер печатает в лог как rsa_fingerprint при старте.
            # Совпало — вшит именно тот ключ, которым сервер отвечает в resPQ.
            report.append("     ключ сервера: fingerprint 0x%016x" % rsa_fingerprint(pem))

    if text == original:
        report.append("FAIL OwpengramServers.java: ничего не заменено")
        return
    write_text(path, text, crlf, raw, args.backup)
    report.append("OK   OwpengramServers.java: %s"
                  % ", ".join("%s=%d" % kv for kv in sorted(counts.items())))


def find_leftovers(root: pathlib.Path) -> list:
    """Где ещё остались следы XTree Gram, кроме gradle.properties.

    gradle.properties исключён сознательно: там `Xtreegram` был только в
    APP_PACKAGE и RELEASE_KEY_*, и оба переписаны регекспами. Если бы мы искали и
    там, проверка падала бы на корректной сборке.
    """
    leftovers = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "gradle.properties" or ".bak" in path.name:
            continue
        if path.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg", ".attheme"):
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = [m for m in JAVA_MARKERS if m in text]
        if found:
            leftovers.append((path.relative_to(root), found))
    return leftovers


def check(root: pathlib.Path, report: list) -> int:
    leftovers = find_leftovers(root)
    if leftovers:
        report.append("FAIL остались следы XTree Gram в %d файлах:" % len(leftovers))
        for rel, found in leftovers[:20]:
            report.append("     %s (%s)" % (rel, ", ".join(found)))
        if len(leftovers) > 20:
            report.append("     ... и ещё %d" % (len(leftovers) - 20))
        return 1
    report.append("OK   следов XTree Gram в дереве не осталось")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tmessages", help="путь к TMessagesProj")
    ap.add_argument("--host", default=None,
                    help="DEFAULT_HOST встроенного сервера; только числовой IPv4")
    ap.add_argument("--port", type=int, default=None, help="DEFAULT_PORT")
    ap.add_argument("--rsa-pem", default=None, help="файл с публичным RSA-ключом сервера")
    ap.add_argument("--version-code", default=None, help="APP_VERSION_CODE")
    ap.add_argument("--version-name", default=None, help="APP_VERSION_NAME")
    ap.add_argument("--keystore-alias", default=None)
    ap.add_argument("--keystore-password", default=None)
    ap.add_argument("--backup", action="store_true",
                    help="класть рядом с каждым файлом .bak-shapito")
    ap.add_argument("--check", action="store_true",
                    help="ничего не менять, только поискать остатки XTree Gram")
    args = ap.parse_args()

    root = pathlib.Path(args.tmessages)
    if not root.is_dir():
        print("FAIL: %s не каталог" % root)
        return 1

    report = []
    report.append("brand.json: %s" % BRAND_PATH)

    if args.check:
        rc = check(root, report)
        print("\n".join(report))
        return rc

    if args.port is None:
        print("FAIL: нужен --port (в overlay стоит порт XTree Gram)")
        return 1
    # Только числовой IPv4. Нативный слой Android (ConnectionSocket.cpp, ветка
    # прямого подключения) зовёт inet_pton и имён не понимает: на домене сокет
    # закрывается сразу, клиент переподключается по кругу и показывает вечную
    # загрузку. Ошибка молчаливая, поэтому отказываемся здесь.
    if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}", args.host or ""):
        print("FAIL: --host %r не числовой IPv4." % args.host)
        print("      Домен здесь не работает: inet_pton в нативном слое Android "
              "имена не резолвит (ConnectionSocket.cpp), приложение уйдёт "
              "в вечную загрузку.")
        return 1
    if any(int(octet) > 255 for octet in args.host.split(".")):
        print("FAIL: --host %r — октет больше 255" % args.host)
        return 1
    if not args.rsa_pem:
        # Без этого в APK остаётся ключ сервера XTree Gram, и клиент не сможет
        # пройти рукопожатие с нашим. Молчаливая сборка тут хуже отказа.
        print("FAIL: нужен --rsa-pem. В overlay лежит RSA-ключ сервера XTree Gram "
              "(2.27.200.203); с ним ShapitoGram к нашему серверу не подключится.")
        return 1

    patch_gradle_properties(root, args, report)
    patch_strings(root, args, report)
    patch_java(root, args, report)
    patch_servers(root, args, report)
    rc = check(root, report)

    print("\n".join(report))
    print()
    print("Напоминание: keystore (alias=%s) должен лежать в TMessagesProj/config/release.keystore"
          % (args.keystore_alias or BRAND["android"]["keystore_alias"]))
    if any(line.startswith("FAIL") for line in report):
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
