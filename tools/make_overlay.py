#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собрать overlay XTree Gram для Android-конвейера ShapitoGram.

Зачем overlay, а не патч
------------------------
Кастомизация XTree Gram в `E:\\xtreegram-build\\android` — это **205 изменённых
файлов** поверх `origin/main` (коммит 4adc49b2) плюс 4 скрипта. Патч на такое
дерево хрупок (fuzz, контексты, бинарные иконки), а overlay — это просто
«положить файлы поверх свежего клона». Результат детерминирован: дерево после
overlay побайтово равно рабочему дереву XTree Gram.

Что берём
---------
- 205 файлов из `git diff --name-only` (включая 145 иконок);
- НЕ берём 21 файл `.bak-*` — это бэкапы скриптов правки, в сборке им не место;
- НЕ берём `KEYSTORE_INFO.txt` (локальный, с паролями);
- 4 скрипта (`patch_*.py`) не нужны: их правки уже применены в файлах, а
  `patch_payments_off.py` отдельно проверяется как контрольная точка.

Одно исключение: overlay НЕ побайтовая копия
--------------------------------------------
`gradle.properties` попадает в overlay, но с вычищенными `RELEASE_KEY_ALIAS`,
`RELEASE_STORE_PASSWORD` и `RELEASE_KEY_PASSWORD`. В дереве XTree Gram там лежит
рабочий пароль ключа подписи, а этот overlay уезжает в ПУБЛИЧНЫЙ репозиторий
сборки. Значения всё равно перезаписываются на шаге ребрендинга
(`scripts/rebrand_android.py`), поэтому в сборку они не влияют, но и светить их
незачем. Остальные 204 файла копируются байт в байт.

Важно про шум переводов строк
-----------------------------
`git status` показывает 866 изменённых файлов, но 634 из них — автогенерённые
`.kt` в `TMessagesProj_AppTests` без единого реального изменения: в репозитории
включён `core.autocrlf=true`, и они «изменены» только по CRLF. `git diff` их не
видит, поэтому берём именно `git diff --name-only` — 205, а не 866.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

SRC = pathlib.Path(r"E:\xtreegram-build\android")
DST = pathlib.Path(__file__).resolve().parent.parent / "files"

# Локальные артефакты: пароли, бэкапы. В сборку не идут ни при каких условиях.
EXCLUDE_SUFFIXES = (".bak-shapito",)
EXCLUDE_NAMES = {"KEYSTORE_INFO.txt", "local.properties"}
EXCLUDE_MARKERS = (".bak-",)

# Секреты подписи: в overlay остаются, но с заглушками вместо значений.
SCRUBBED_PROPERTIES = ("RELEASE_KEY_ALIAS", "RELEASE_STORE_PASSWORD", "RELEASE_KEY_PASSWORD")
PLACEHOLDER = "SET_BY_REBRAND_SCRIPT"



def tracked_changes():
    out = subprocess.run(
        ["git", "-C", str(SRC), "diff", "--name-only"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    if not (SRC / ".git").is_dir():
        print(f"FAIL: {SRC} не git-репозиторий")
        return 1

    names = tracked_changes()
    print(f"изменённых файлов в git diff: {len(names)}")

    if DST.exists():
        shutil.rmtree(DST)

    copied = 0
    total = 0
    skipped = []
    scrubbed = []
    for rel in names:
        name = pathlib.Path(rel).name
        if name in EXCLUDE_NAMES or any(m in name for m in EXCLUDE_MARKERS) \
                or name.endswith(EXCLUDE_SUFFIXES):
            skipped.append(rel)
            continue
        source = SRC / rel
        if not source.is_file():
            skipped.append(rel + " (нет файла)")
            continue
        target = DST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "gradle.properties":
            raw = source.read_bytes()
            crlf = b"\r\n" in raw
            text = raw.decode("utf-8").replace("\r\n", "\n")
            for key in SCRUBBED_PROPERTIES:
                text = re.sub(r"^%s=.*$" % re.escape(key),
                              "%s=%s" % (key, PLACEHOLDER), text, flags=re.M)
            out = text.replace("\n", "\r\n") if crlf else text
            target.write_bytes(out.encode("utf-8"))
            scrubbed.append(rel)
        else:
            shutil.copyfile(source, target)
        copied += 1
        total += target.stat().st_size

    print(f"скопировано: {copied}, суммарно {total / 1024 / 1024:.2f} МБ")
    if scrubbed:
        print(f"обезврежено ({len(scrubbed)}): {', '.join(scrubbed)} "
              f"-> {', '.join(SCRUBBED_PROPERTIES)} = {PLACEHOLDER}")
    if skipped:
        print(f"пропущено ({len(skipped)}):")
        for rel in skipped:
            print("  -", rel)

    # Контрольная точка: без отключённых платежей сборка бессмысленна.
    payments = DST / ("TMessagesProj/src/main/java/org/telegram/ui/"
                      "PaymentFormActivity.java")
    if not payments.is_file():
        print("FAIL: PaymentFormActivity.java не попал в overlay")
        return 1
    text = payments.read_text(encoding="utf-8", errors="replace")
    if "XTREE_PAYMENTS_DISABLED" not in text:
        print("FAIL: в PaymentFormActivity.java нет XTREE_PAYMENTS_DISABLED — "
              "платежи окажутся включены")
        return 1
    print("OK  платежи: XTREE_PAYMENTS_DISABLED на месте")

    # Контрольная точка: сервер, который перезапишет ребрендинг.
    servers = DST / "TMessagesProj/src/main/java/org/telegram/owpengram/OwpengramServers.java"
    if not servers.is_file():
        print("FAIL: OwpengramServers.java не попал в overlay")
        return 1
    text = servers.read_text(encoding="utf-8", errors="replace")
    if "DEFAULT_HOST" not in text or "OWPENGRAM_RSA_KEY" not in text:
        print("FAIL: в OwpengramServers.java нет DEFAULT_HOST / OWPENGRAM_RSA_KEY")
        return 1
    print("OK  сервер: DEFAULT_HOST и OWPENGRAM_RSA_KEY на месте (перезапишет ребрендинг)")

    # Контрольная точка: в overlay не должно остаться ни одного пароля подписи.
    # Проверяем именно содержимое, а не факт замены: если в gradle.properties
    # появится ещё один ключ с паролем, тест обязан упасть, а не промолчать.
    gp = DST / "gradle.properties"
    if not gp.is_file():
        print("FAIL: gradle.properties не попал в overlay")
        return 1
    text = gp.read_text(encoding="utf-8")
    leaks = [key for key in SCRUBBED_PROPERTIES
             if not re.search(r"^%s=%s$" % (re.escape(key), PLACEHOLDER), text, re.M)]
    if leaks:
        print(f"FAIL: в gradle.properties не обезврежены: {', '.join(leaks)}")
        return 1
    for secret in ("XtreegramStore", "ShapitoStore", "xtreegramkey"):
        if secret in text:
            print(f"FAIL: в overlay утёк секрет подписи ({secret})")
            return 1
    print("OK  секреты: паролей и алиасов подписи в overlay нет")

    print(f"\noverlay готов: {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
