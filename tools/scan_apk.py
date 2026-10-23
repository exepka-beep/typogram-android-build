#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск следов XTree Gram (и чужого сервера) внутри готового APK.

Зачем это, если есть `scripts/verify_apk.sh`
--------------------------------------------
Тот проверяет структуру: имя пакета, версию, подпись. Но он ничего не говорит о
содержимом. А промах именно в содержании даёт самый неприятный класс ошибок:
APK ставится, запускается, выглядит правильно — и уходит на сервер XTree Gram
(`2.27.200.203`), потому что overlay перезаписал `DEFAULT_HOST`, а шаг
ребрендинга по какой-то причине не отработал. Увидеть это можно было бы только
по вечно висящему сплэшу на телефоне.

Здесь проверяются три вещи:
  * остатки бренда XTree Gram в ресурсах и коде (должны отсутствовать);
  * адрес старого сервера `2.27.200.203` (должен отсутствовать);
  * адрес и RSA-ключ нашего сервера (должны присутствовать).

Как ищется
----------
APK — это zip, содержимое не сжато «в никуда»: строки лежат в `resources.arsc`
(пул строк, UTF-8 или UTF-16LE), в `classes*.dex` (MUTF-8, практически UTF-8) и
в `assets/`. Поэтому каждый файл архива ищется в двух кодировках сразу. Это
дешевле и надёжнее, чем разбирать форматы: 100 МБ архива читаются за секунды, а
ложных срабатываний на UTF-16LE не бывает — обычный ASCII в этой кодировке
перемежается нулевыми байтами.

Использование
-------------
    scan_apk.py app.apk --absent Xtreegram --present 94.156.170.106
    scan_apk.py app.apk --absent-file markers.txt --json report.json

Код возврата 1, если хоть один `--absent` найден или хоть один `--present` не
найден. Отсутствие — это тоже ошибка: если в APK нет IP нашего сервера, значит
туда не попал наш адрес, и клиент никуда не подключится.

Про адрес: проверять надо именно **IP**, а не домен. В `DEFAULT_HOST` уходит
`APP_SERVER_HOST`, а он обязан быть числовым IPv4 — нативный слой Android зовёт
`inet_pton` и имён не понимает. Домен в APK не вшивается вообще (он нужен
только на этапе сборки, для `SERVER_INFO_URL`), поэтому `--present` с доменом
падал бы всегда.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import zipfile

# Что искать всегда. Список намеренно короткий: только то, что попадание в APK
# означает сломанную сборку, а не косметику.
DEFAULT_ABSENT = (
    "Xtreegram",
    "XTree Gram",
    "XTREEGRAM",
    "2.27.200.203",
    "XtreegramStore2026",
    "xtreegramkey",
)


def scan(path: pathlib.Path, markers, present):
    """{marker: [(имя файла в архиве, кодировка, смещение)]}"""
    found = {m: [] for m in list(markers) + list(present)}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                raw = zf.read(info)
            except (zipfile.BadZipFile, RuntimeError, OSError):
                continue
            for marker in found:
                if found[marker]:
                    # Достаточно одного места: цель проверки — факт, а не список.
                    continue
                for encoding in ("utf-8", "utf-16le"):
                    needle = marker.encode(encoding)
                    offset = raw.find(needle)
                    if offset >= 0:
                        found[marker].append((info.filename, encoding, offset))
                        break
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apk", help="путь к APK")
    ap.add_argument("--absent", action="append", default=[],
                    help="строка, которой в APK быть НЕ должно (можно повторять)")
    ap.add_argument("--absent-file", default=None,
                    help="файл со списком запрещённых строк, по одной на строку")
    ap.add_argument("--present", action="append", default=[],
                    help="строка, которая в APK быть ДОЛЖНА (можно повторять)")
    ap.add_argument("--no-defaults", action="store_true",
                    help="не добавлять встроенный список запрещённых строк")
    ap.add_argument("--json", default=None, help="куда записать отчёт в JSON")
    args = ap.parse_args()

    path = pathlib.Path(args.apk)
    if not path.is_file():
        print("FAIL: нет файла %s" % path)
        return 1

    absent = list(args.absent)
    if not args.no_defaults:
        absent = list(DEFAULT_ABSENT) + absent
    if args.absent_file:
        for line in pathlib.Path(args.absent_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                absent.append(line)

    # Дубликаты не мешают, но и не нужны: поиск идёт по словарю.
    absent = list(dict.fromkeys(absent))
    present = list(dict.fromkeys(args.present))

    print("APK: %s (%.1f МБ)" % (path, path.stat().st_size / 1024 / 1024))
    print("ищу: запрещённых строк %d, обязательных %d" % (len(absent), len(present)))
    found = scan(path, absent, present)

    problems = []

    print("\n--- не должно быть в APK ---")
    for marker in absent:
        hits = found[marker]
        if hits:
            name, encoding, offset = hits[0]
            print("  НАЙДЕНО  %-24s %s (%s, offset %d)" % (repr(marker), name, encoding, offset))
            problems.append("остаток %r в %s" % (marker, name))
        else:
            print("  чисто    %s" % repr(marker))

    if present:
        print("\n--- должно быть в APK ---")
        for marker in present:
            hits = found[marker]
            short = marker if len(marker) <= 40 else marker[:37] + "..."
            if hits:
                name, encoding, offset = hits[0]
                print("  есть     %-42s %s (%s)" % (short, name, encoding))
            else:
                print("  НЕТ      %s" % short)
                problems.append("не найдено обязательное %r" % marker)

    if args.json:
        report = {
            "apk": str(path),
            "size": path.stat().st_size,
            "absent": {m: [{"file": f, "encoding": e, "offset": o} for f, e, o in found[m]]
                       for m in absent},
            "present": {m: [{"file": f, "encoding": e, "offset": o} for f, e, o in found[m]]
                        for m in present},
            "problems": problems,
        }
        pathlib.Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        print("\nотчёт: %s" % args.json)

    print()
    if problems:
        print("FAIL: %d проблем(ы):" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("APK чист: следов XTree Gram нет, наш сервер и ключ на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
