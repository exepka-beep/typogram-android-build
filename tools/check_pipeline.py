#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Локальная проверка Android-конвейера до заливки в GitHub.

Зачем
-----
Прогон в Actions стоит 40–70 минут, и почти всё, что может пойти не так, видно
заранее и за секунды: не хватает файла, разошлись версии, в overlay остался
пароль, в workflow сослались на секрет, которого никто не ставит, `EXPECTED_CERT`
не совпал с ключом. Каждая такая ошибка в CI стоит целого прогона и разбора
логов — здесь она стоит одной строки.

Проверяется
-----------
  1. на месте ли все файлы конвейера;
  2. overlay: 205 файлов, платежи отключены, ключ сервера на месте, паролей нет;
  3. brand.json: правила замены, отсутствие пароля, отпечаток ключа;
  4. workflow: YAML разбирается, версия сходится с versionCode, секреты объявлены
     и кем-то ставятся, ссылки на скрипты существуют;
  5. keystore: файл есть, alias и пароль совпадают с brand.json;
  6. все Python-скрипты компилируются.

Запуск
------
    <venv>/python tools/check_pipeline.py

Нужен PyYAML (для разбора workflow). Путь ищется по переменной окружения
PYTHONPATH обычным образом; в этом проекте рабочий интерпретатор —
C:/Users/rgali/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe.
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

REQUIRED_FILES = [
    ".github/workflows/build-apk.yml",
    "brand.json",
    "push_to_github.py",
    "scripts/fetch_server_key.py",
    "scripts/rebrand_android.py",
    "scripts/set_api_credentials.py",
    "scripts/verify_apk.sh",
    "tools/make_overlay.py",
    "tools/run_build.py",
    "tools/scan_apk.py",
    "tools/set_secrets.py",
    "README.md",
]

# Секреты, которые workflow обязан использовать и которые кто-то обязан ставить.
# Пары «объявлено в workflow» -> «ставится в set_secrets.py» должны сойтись:
# иначе сборка падает на середине с пустой переменной.
REQUIRED_SECRETS = {
    "ANDROID_KEYSTORE_BASE64": "tools/set_secrets.py",
    "ANDROID_KEYSTORE_PASSWORD": "tools/set_secrets.py",
    "ANDROID_KEY_ALIAS": "tools/set_secrets.py",
}
OPTIONAL_SECRETS = {"TELEGRAM_API_ID", "TELEGRAM_API_HASH"}

# 42, а не 205: из overlay TypoGram намеренно убраны 163 файла брендовой
# графики (145 иконок + 18 icon_*.xml). Заказчик просил логотип как у Telegram,
# поэтому картинки не переопределяются и берутся из upstream. Остались только
# текстовые правки: Java, strings.xml, styles.xml, gradle.properties и
# ConnectionSocket.cpp с резолвом домена.
OVERLAY_FILE_COUNT = 42
XTREE_MARKERS = ("XTree Gram", "Xtreegram", "XTREEGRAM")
SECRET_STRINGS = ("XtreegramStore", "ShapitoStore", "xtreegramkey")

problems: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files() -> None:
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            fail("нет файла: %s" % rel)


def check_overlay() -> None:
    files_dir = ROOT / "files"
    if not files_dir.is_dir():
        fail("нет каталога files/ — overlay не собран (tools/make_overlay.py)")
        return

    all_files = [p for p in files_dir.rglob("*") if p.is_file()]
    if len(all_files) != OVERLAY_FILE_COUNT:
        fail("overlay: %d файлов, ожидали %d — пересобери tools/make_overlay.py"
             % (len(all_files), OVERLAY_FILE_COUNT))

    payments = files_dir / ("TMessagesProj/src/main/java/org/telegram/ui/"
                            "PaymentFormActivity.java")
    if not payments.is_file():
        fail("overlay: нет PaymentFormActivity.java — платежи окажутся включены")
    else:
        text = read(payments)
        if "XTREE_PAYMENTS_DISABLED = true" not in text:
            fail("overlay: в PaymentFormActivity.java нет 'XTREE_PAYMENTS_DISABLED = true'")
        if "if (XTREE_PAYMENTS_DISABLED)" not in text:
            fail("overlay: флаг платежей есть, но onFragmentCreate его не проверяет")

    servers = files_dir / ("TMessagesProj/src/main/java/org/telegram/owpengram/"
                           "OwpengramServers.java")
    if not servers.is_file():
        fail("overlay: нет OwpengramServers.java")
    else:
        text = read(servers)
        for marker in ("DEFAULT_HOST", "DEFAULT_PORT", "OWPENGRAM_RSA_KEY"):
            if marker not in text:
                fail("overlay: в OwpengramServers.java нет %s" % marker)
        if 'DEFAULT_HOST = "2.27.200.203"' not in text:
            fail("overlay: в OwpengramServers.java ожидался адрес XTree Gram "
                 "(2.27.200.203) — если он уже переписан, overlay собран не с чистого дерева")

    gradle = files_dir / "gradle.properties"
    if not gradle.is_file():
        fail("overlay: нет gradle.properties")
    else:
        text = read(gradle)
        for key in ("RELEASE_KEY_ALIAS", "RELEASE_STORE_PASSWORD", "RELEASE_KEY_PASSWORD"):
            if not re.search(r"^%s=SET_BY_REBRAND_SCRIPT$" % key, text, re.M):
                fail("overlay: %s в gradle.properties не обезврежен "
                     "(в публичном репозитории окажется рабочий пароль)" % key)
        for secret in SECRET_STRINGS:
            if secret in text:
                fail("overlay: в gradle.properties утёк секрет подписи (%s)" % secret)
        if "APP_PACKAGE=com.xtreegram.messenger" not in text:
            fail("overlay: APP_PACKAGE в gradle.properties уже не XTree Gram — "
                 "шаг ребрендинга может не сработать")
        match = re.search(r"^APP_VERSION_CODE=(\d+)$", text, re.M)
        if not match:
            fail("overlay: не нашёл APP_VERSION_CODE в gradle.properties")


def check_brand() -> dict:
    path = ROOT / "brand.json"
    if not path.is_file():
        fail("нет brand.json")
        return {}
    try:
        brand = json.loads(read(path))
    except json.JSONDecodeError as exc:
        fail("brand.json не разбирается: %s" % exc)
        return {}

    sources = {r["from"] for r in brand["replace"]["rules"]}
    for marker in XTREE_MARKERS:
        if marker not in sources:
            fail("brand.json: нет правила замены для %r — XTree Gram останется в APK"
                 % marker)
    if "Telegram" not in sources:
        fail("brand.json: нет правила замены для 'Telegram'")

    android = brand.get("android", {})
    if "keystore_password" in android:
        fail("brand.json: есть keystore_password — файл уезжает в публичный "
             "репозиторий, пароль должен приходить из секрета")
    if not android.get("keystore_cert_sha256"):
        fail("brand.json: нет keystore_cert_sha256 — нечем сверять подпись APK")
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", str(android.get("package") or "")):
        fail("brand.json: android.package = %r не похож на applicationId" % android.get("package"))

    # Пакет должен совпадать в трёх местах, иначе APK поставится как чужое приложение.
    workflow = read(ROOT / ".github/workflows/build-apk.yml")
    match = re.search(r"^\s*APP_PACKAGE:\s*(\S+)$", workflow, re.M)
    if match and match.group(1) != android.get("package"):
        fail("APP_PACKAGE в workflow (%s) не совпадает с brand.json (%s)"
             % (match.group(1), android.get("package")))

    return brand


def check_workflow(brand: dict) -> None:
    try:
        import yaml
    except ImportError:
        fail("нет PyYAML — разбор workflow пропущен "
             "(запусти из venv: binaries/python/envs/default)")
        return

    path = ROOT / ".github/workflows/build-apk.yml"
    if not path.is_file():
        return
    text = read(path)
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        fail("workflow не разбирается как YAML: %s" % exc)
        return

    if "workflow_dispatch" not in (workflow.get("on") or workflow.get(True) or {}):
        # `on:` разбирается как булев ключ True: в YAML 1.1 (а PyYAML следует ему)
        # слова on/off/yes/no — это true/false. Поэтому ищем и по "on", и по True.
        fail("workflow: нет триггера workflow_dispatch")

    job = (workflow.get("jobs") or {}).get("build")
    if not job:
        fail("workflow: нет job'а build")
        return
    if job.get("runs-on") != "ubuntu-latest":
        fail("workflow: runs-on=%r, ожидали ubuntu-latest (сборка идёт в Docker)"
             % job.get("runs-on"))

    env = job.get("env") or {}
    for key in ("UPSTREAM_REF", "APP_SERVER_HOST", "APP_SERVER_PORT", "SERVER_DOMAIN",
                "SERVER_INFO_URL",
                "APP_VERSION_CODE", "APP_VERSION_NAME", "APP_PACKAGE",
                "EXPECTED_VERSION_CODE", "EXPECTED_CERT_SHA256", "GRADLE_TASKS"):
        if key not in env:
            fail("workflow: в env job'а нет %s" % key)

    # Адрес сервера: числовой IPv4 либо имя хоста.
    #
    # Нативный слой Android сам по себе резолвит адрес через inet_pton
    # (TMessagesProj/jni/tgnet/ConnectionSocket.cpp, ветка прямого подключения)
    # и на неудаче закрывает сокет — с доменом это «вечная загрузка», ровно так
    # провалилась сборка ShapitoGram #5. Поэтому домен допустим ТОЛЬКО вместе с
    # патчем из overlay: он при провале inet_pton зовёт gethostbyname. Проверяем
    # обе вещи сразу — и адрес, и наличие патча, иначе APK соберётся и не
    # подключится, а искать причину придётся на телефоне.
    host = str(env.get("APP_SERVER_HOST", ""))
    if re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}", host):
        for octet in host.split("."):
            if int(octet) > 255:
                fail("workflow: APP_SERVER_HOST=%r — октет %s больше 255" % (host, octet))
    elif re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        socket_patch = ROOT / "files/TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
        if not socket_patch.is_file():
            fail("workflow: APP_SERVER_HOST=%r — домен, но в overlay нет "
                 "ConnectionSocket.cpp с резолвом имени" % host)
        if "TypoGram: встроенный сервер задан доменом" not in socket_patch.read_text(
                encoding="utf-8", errors="replace"):
            fail("workflow: APP_SERVER_HOST=%r — домен, но ConnectionSocket.cpp "
                 "не пропатчен (нет gethostbyname после inet_pton)" % host)
    else:
        fail("workflow: APP_SERVER_HOST=%r не похож ни на IPv4, ни на имя хоста" % host)

    port = str(env.get("APP_SERVER_PORT", ""))
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        fail("workflow: APP_SERVER_PORT=%r не похож на номер порта" % port)

    # Старые имена не должны пережить переименование: `CUSTOM_HOST` содержал
    # домен, и если он где-то остался, шаг ребрендинга снова вшил бы домен.
    for stale in ("CUSTOM_HOST", "CUSTOM_PORT"):
        if stale in text:
            fail("workflow: осталась ссылка на %s — адрес сервера задаётся "
                 "APP_SERVER_HOST (IPv4), см. комментарий там" % stale)

    # versionCode afat = APP_VERSION_CODE * 10 + 9 (abiVersionCode=9 в build.gradle).
    try:
        expected = int(env["APP_VERSION_CODE"]) * 10 + 9
        declared = int(env["EXPECTED_VERSION_CODE"])
        if expected != declared:
            fail("workflow: EXPECTED_VERSION_CODE=%d, а из APP_VERSION_CODE=%s "
                 "получается %d (versionCode = APP_VERSION_CODE * 10 + 9)"
                 % (declared, env["APP_VERSION_CODE"], expected))
    except (KeyError, ValueError) as exc:
        fail("workflow: не смог посчитать versionCode: %s" % exc)

    if brand:
        brand_cert = (brand.get("android") or {}).get("keystore_cert_sha256")
        if brand_cert and env.get("EXPECTED_CERT_SHA256") != brand_cert:
            fail("workflow: EXPECTED_CERT_SHA256 не совпадает с brand.json "
                 "android.keystore_cert_sha256")

    if env.get("UPSTREAM_REF") and not re.fullmatch(r"[0-9a-f]{40}", str(env["UPSTREAM_REF"])):
        fail("workflow: UPSTREAM_REF=%r не похож на полный SHA коммита" % env["UPSTREAM_REF"])

    # Контексты, недоступные в env на уровне job. Ошибка тут не в раннере, а на
    # этапе dispatch: GitHub отвечает 422 "failed to parse workflow" и сборка
    # не запускается вообще, так что локальная проверка экономит целый круг.
    allowed_in_job_env = ("github.", "env.", "vars.", "secrets.", "inputs.")
    for key, value in env.items():
        for expression in re.findall(r"\$\{\{\s*([^}]+?)\s*\}\}", str(value)):
            head = expression.split(".")[0] + "."
            if head not in allowed_in_job_env:
                fail("workflow: в env job'а %s использует ${{ %s }} — контекст %r "
                     "там недоступен, GitHub не разберёт workflow (HTTP 422)"
                     % (key, expression, expression.split(".")[0]))

    # Пути, которые задаются первым шагом через $GITHUB_ENV: если шаг пропадёт
    # или переменная переименуется, все остальные шаги получат пустые строки.
    for var in ("SOURCE_DIR", "BUILD_DIR", "GRADLE_HOME"):
        if 'echo "%s=' % var not in text or '>> "$GITHUB_ENV"' not in text:
            fail("workflow: %s не задаётся через $GITHUB_ENV" % var)

    # Шаги: набор обязательных имён. Проверяем имена, а не номера, чтобы порядок
    # можно было менять, не ломая проверку.
    step_names = [s.get("name", "") for s in (job.get("steps") or [])]
    for needle, why in (
        ("Clone upstream", "без клона нечего overlay'ить"),
        ("Update submodules", "без ffmpeg/libvpx нативная часть не соберётся"),
        ("apply the TypoGram overlay", "иначе соберётся чистый upstream"),
        ("Verify payments are disabled", "платежи — требование заказчика"),
        ("Install the TypoGram server RSA public key", "иначе клиент не пройдёт рукопожатие"),
        ("Install the signing keystore", "без ключа нет подписи"),
        ("Rebrand to TypoGram", "имя пакета, строки, адрес сервера"),
        ("Pull the base image", "Docker Hub отдаёт 429 анонимным клиентам — нужен фолбэк на зеркало"),
        ("Build the builder image", "без образа с SDK и NDK сборки нет"),
        ("Verify the APK structure and signature", "проверка подписи и пакета"),
        ("Scan the APK for XTree Gram leftovers", "проверка содержимого APK"),
        ("Upload the APK", "результат должен быть скачиваемым"),
    ):
        if not any(needle.lower() in name.lower() for name in step_names):
            fail("workflow: нет шага %r (%s)" % (needle, why))

    # Путь к APK внутри контейнера. $BUILD_DIR монтируется в /work, поэтому
    # одного отрезания префикса мало: получится /TMessagesProj_App/... и шаг
    # упадёт на «нет файла». Уже случалось в прогоне #4.
    verify_steps = [s for s in (job.get("steps") or [])
                    if "verify_apk.sh" in json.dumps(s, ensure_ascii=False)]
    if not verify_steps:
        fail("workflow: ни один шаг не запускает scripts/verify_apk.sh")
    for step in verify_steps:
        body = json.dumps(step, ensure_ascii=False)
        if "/work${" not in body and "/work$" not in body:
            fail("workflow: шаг %r передаёт путь к APK без префикса /work — "
                 "внутри контейнера такой файл не найдётся (смонтировано в /work)"
                 % step.get("name", "?"))

    # Сканер содержимого обязан искать в APK именно IP, а не домен: в бинарь
    # попадает DEFAULT_HOST, а туда теперь идёт APP_SERVER_HOST. Домен в APK не
    # вшивается вообще (он живёт только в SERVER_INFO_URL, и тот нужен лишь на
    # этапе сборки), поэтому `--present "$SERVER_DOMAIN"` упал бы всегда.
    scan_steps = [s for s in (job.get("steps") or [])
                  if "scan_apk.py" in (s.get("run") or "")]
    if not scan_steps:
        fail("workflow: ни один шаг не запускает tools/scan_apk.py")
    for step in scan_steps:
        # Берём сырой текст `run`, а не json.dumps: в дампе кавычки экранированы
        # (\"), и поиск по '"$APP_SERVER_HOST"' не нашёл бы ничего.
        body = step.get("run") or ""
        if '"$APP_SERVER_HOST"' not in body:
            fail("workflow: шаг %r не проверяет наличие APP_SERVER_HOST в APK — "
                 "именно этот адрес вшивается в клиент" % step.get("name", "?"))
        if '"$SERVER_DOMAIN"' in body:
            fail("workflow: шаг %r требует наличия в APK домена SERVER_DOMAIN, "
                 "а он туда не вшивается — проверка упала бы всегда"
                 % step.get("name", "?"))

    # Зеркало базового образа: без него сборка падает на `429 Too Many Requests`
    # от registry-1.docker.io, и это уже случалось.
    if "public.ecr.aws/docker/library/gradle" not in text:
        fail("workflow: базовый образ не тянется с зеркала — Docker Hub отдаст 429")
    if 'sed -i "1s|^FROM ' not in text:
        fail("workflow: строка FROM в Dockerfile не переписывается на зеркало — "
             "docker build пойдёт в Docker Hub за тем же 429")

    # Ссылки на файлы конвейера, которые workflow реально запускает.
    for rel in re.findall(r"scripts/[\w.]+\.(?:py|sh)", text):
        if not (ROOT / rel).is_file():
            fail("workflow ссылается на %s, а такого файла нет" % rel)

    # Секреты: объявлены в workflow -> кто-то их ставит. Цифры в имени обязательны:
    # ANDROID_KEYSTORE_BASE64 без них не находится, и проверка тихо пропускает
    # главный секрет — keystore.
    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    for name in sorted(used - OPTIONAL_SECRETS):
        if name not in REQUIRED_SECRETS:
            fail("workflow использует секрет %s, но он не учтён в проверке" % name)
    for name in REQUIRED_SECRETS:
        if name not in used:
            fail("workflow не использует обязательный секрет %s" % name)
        setter = ROOT / REQUIRED_SECRETS[name]
        if setter.is_file() and name not in read(setter):
            fail("%s должен ставить секрет %s, но в нём его нет"
                 % (REQUIRED_SECRETS[name], name))


def check_keystore(brand: dict) -> None:
    keystore = ROOT.parent / "artifacts" / "release.keystore"
    if not keystore.is_file():
        fail("нет keystore: %s (нужен, чтобы положить его в секреты)" % keystore)
        return

    info = keystore.parent / "KEYSTORE_INFO.txt"
    if not info.is_file():
        fail("нет %s — из него берётся пароль для секретов" % info)
        return
    text = read(info)
    match = re.search(r"^Пароль:\s*(\S+)", text, re.M)
    if not match:
        fail("в KEYSTORE_INFO.txt нет строки 'Пароль:'")
        return
    if brand:
        alias = (brand.get("android") or {}).get("keystore_alias")
        if alias and alias not in text:
            fail("в KEYSTORE_INFO.txt нет alias %r из brand.json" % alias)
        cert = (brand.get("android") or {}).get("keystore_cert_sha256")
        if cert and cert not in text:
            fail("отпечаток в KEYSTORE_INFO.txt не совпадает с brand.json")


def check_scripts_compile() -> None:
    scripts = [
        "push_to_github.py",
        "tools/run_build.py",
        "tools/scan_apk.py",
        "tools/set_secrets.py",
        "tools/make_overlay.py",
        "scripts/rebrand_android.py",
        "scripts/fetch_server_key.py",
        "scripts/set_api_credentials.py",
    ]
    for rel in scripts:
        path = ROOT / rel
        if not path.is_file():
            continue
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            fail("%s не компилируется: %s" % (rel, proc.stderr.strip().splitlines()[-1:]))

    sh = ROOT / "scripts/verify_apk.sh"
    if sh.is_file():
        proc = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        if proc.returncode != 0:
            fail("scripts/verify_apk.sh: синтаксическая ошибка: %s" % proc.stderr.strip())


def check_token() -> None:
    token_file = ROOT / ".gh_token"
    if token_file.is_file():
        print("токен: .gh_token на месте (%d символов)"
              % len(token_file.read_text(encoding="utf-8").strip()))
    else:
        print("токен: .gh_token нет — заливка возьмёт $GH_TOKEN или --token")

    # Токен не должен уехать в репозиторий сборки.
    push = ROOT / "push_to_github.py"
    if push.is_file():
        text = read(push)
        if '".gh_token"' not in text:
            fail("push_to_github.py: .gh_token не исключён из заливки")


def main() -> int:
    print("конвейер: %s\n" % ROOT)
    check_required_files()
    check_overlay()
    brand = check_brand()
    check_workflow(brand)
    check_keystore(brand)
    check_scripts_compile()
    check_token()

    print()
    if problems:
        print("НАЙДЕНО ПРОБЛЕМ: %d" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("все проверки пройдены — конвейер можно заливать.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
