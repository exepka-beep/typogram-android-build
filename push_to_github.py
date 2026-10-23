#!/usr/bin/env python3
"""Залить конвейер Android в репозиторий, на котором собирается APK.

Почему Git Data API, а не Contents API
--------------------------------------
iOS-конвейер заливается через Contents API: 19 файлов, все текстовые, и там это
удобно. Здесь файлов 226, из них 145 — бинарные иконки, и суммарно 9.65 МБ.
Contents API дал бы 226 отдельных коммитов (и 226 последовательных PUT), а
главное — он молча ломается на больших файлах. Git Data API делает то же самое
одним атомарным коммитом: блобы создаются параллельно, дерево и коммит — по
одному запросу.

Блобы всё равно по одному запросу на файл (иначе API не умеет), поэтому они
уходят в несколько потоков с повторами на вторичный rate limit: 9.65 МБ и 217
запросов — это пара минут.

Два ограничения API, на которые здесь есть ответ
------------------------------------------------
1. `POST /git/blobs` на репозитории без коммитов отвечает `409 Git Repository is
   empty` — блоб некуда положить. Первый файл поэтому кладётся через Contents API
   (он же создаёт ветку), см. `seed_empty_repo`.
2. При параллельных запросах GitHub включает ВТОРИЧНЫЙ rate limit и отвечает
   `403 You have exceeded a secondary rate limit`. Он не про 5000/час, а про
   всплески, и отпускает через секунды. Потоков поэтому немного (3), и каждый
   запрос повторяется с экспоненциальной паузой.

Что НЕ заливается
-----------------
`.gh_token`, `artifacts/`, `__pycache__`, драйвер `tools/run_build.py` и
`tools/set_secrets.py`. Репозиторий сборки может быть публичным — локальные
помощники, умеющие дёргать GitHub API от имени владельца и раскладывать
секреты, в нём не нужны.

Сам этот файл уезжает: токена в нём нет, он читается из `.gh_token` / `$GH_TOKEN`
(см. `read_token`), а сам скрипт полезен как часть воспроизводимого конвейера.

Использование
-------------
    python push_to_github.py                 # обновить существующий репозиторий
    python push_to_github.py --create --public
    python push_to_github.py --list          # показать, что уедет
    python push_to_github.py --dry-run       # посчитать, но не отправлять

Токен: --token, $GH_TOKEN или файл .gh_token рядом с этим скриптом.
Владелец/репозиторий: --owner/--repo, $GH_OWNER/$GH_REPO или значения ниже.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import pathlib
import random
import time
import urllib.error
import urllib.request

OWNER = os.environ.get("GH_OWNER", "exepka-beep")
REPO = os.environ.get("GH_REPO", "shapitogram-android-build")
BRANCH = os.environ.get("GH_BRANCH", "main")
API = "https://api.github.com"

ROOT = pathlib.Path(__file__).resolve().parent
TOKEN_FILE = ROOT / ".gh_token"

# Не уезжает никогда. Сравнение по точному пути или префиксу каталога.
EXCLUDE_PATHS = (
    ".gh_token",
    "artifacts",
    "__pycache__",
    "tools/run_build.py",
    "tools/set_secrets.py",
)
EXCLUDE_SUFFIXES = (".pyc",)
EXCLUDE_MARKERS = (".bak-",)

# Переводы строк нормализуем только здесь. Всё остальное — байт в байт:
# 145 иконок (webp/png) и night.attheme, который разбирается побайтово.
TEXT_SUFFIXES = (".yml", ".yaml", ".sh", ".md", ".py", ".json", ".properties",
                 ".xml", ".java", ".txt", ".gitattributes", ".cfg", ".pro")

# Генерируется на лету и уезжает вместе с остальным.
GITATTRIBUTES = """\
# Файлы overlay нормализованы ещё до загрузки: текст переведён в LF, бинарники
# не тронуты. Конвертировать их повторно на checkout нельзя.
#
# Почему это важно: night.attheme разбирается побайтово и делится по '\\n'.
# С CRLF каждое значение получает хвостовой '\\r', Utilities.parseInt() бросает
# исключение, исключение проглатывается — и все цвета темы становятся нулём.
# В тёмной теме это чёрный экран без текста. Диагностировать такое в собранном
# APK крайне неприятно, поэтому конвертацию выключаем для всего репозитория.
* -text
"""


def resolve_token(cli_token):
    if cli_token:
        return cli_token.strip()
    env = os.environ.get("GH_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit(
        "нет токена. Задай GH_TOKEN, положи .gh_token рядом с этим файлом "
        "или передай --token.\n"
        "Нужен classic PAT с галочками `repo` и `workflow` (без `workflow` GitHub "
        "не даст залить .github/workflows/)."
    )


def request(url, token, method="GET", payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "shapitogram-android-pipeline")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def build_manifest():
    """[(путь, режим, содержимое)] — что именно уедет в репозиторий.

    Содержимое читается один раз: и для `--list`, и для подсчёта, и для отправки
    нужен один и тот же набор байт, а overlay — это 9.65 МБ, которые незачем
    перечитывать на каждый шаг.
    """
    manifest = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == e or rel.startswith(e + "/") for e in EXCLUDE_PATHS):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if any(m in path.name for m in EXCLUDE_MARKERS):
            continue
        raw = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            raw = raw.replace(b"\r\n", b"\n")
        mode = "100755" if path.suffix == ".sh" else "100644"
        manifest.append((rel, mode, raw))
    manifest.append((".gitattributes", "100644", GITATTRIBUTES.encode("utf-8")))
    return manifest


def token_scopes(token):
    req = urllib.request.Request("%s/user" % API)
    req.add_header("Authorization", "token %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "shapitogram-android-pipeline")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            scopes = resp.headers.get("X-OAuth-Scopes")
            login = json.loads(resp.read().decode("utf-8")).get("login", "?")
            return login, ([s.strip() for s in scopes.split(",") if s.strip()]
                           if scopes is not None else None)
    except urllib.error.HTTPError as exc:
        raise SystemExit("токен не принят: HTTP %s %s"
                         % (exc.code, exc.read().decode("utf-8", "replace")[:200]))


def require_scopes(token):
    login, scopes = token_scopes(token)
    if scopes is None:
        print("токен: %s (fine-grained — scope'ы отсюда не видны)" % login)
        return
    print("токен: %s, scopes: %s" % (login, ", ".join(scopes)))
    missing = [s for s in ("repo", "workflow") if s not in scopes]
    if missing:
        raise SystemExit(
            "у токена нет scope: %s\n"
            "  `repo`     — заливать файлы;\n"
            "  `workflow` — трогать файлы в .github/workflows/.\n"
            "Открой https://github.com/settings/tokens, добавь галочки и создай "
            "токен заново: у classic PAT scope'ы меняются только пересозданием."
            % ", ".join(missing))


def ensure_repo(token, owner, repo, private):
    status, data = request("%s/repos/%s/%s" % (API, owner, repo), token)
    if status == 200:
        print("репозиторий есть: %s/%s (private=%s)"
              % (owner, repo, data.get("private")))
        return True
    if status != 404:
        print("проверка репозитория: HTTP %s %s" % (status, str(data)[:200]))
        return False

    status, user = request("%s/user" % API, token)
    me = user.get("login", "") if isinstance(user, dict) else ""
    if me.lower() == owner.lower():
        url = "%s/user/repos" % API
        payload = {"name": repo, "private": private, "auto_init": False,
                   "description": "ShapitoGram Android APK build pipeline"}
    else:
        url = "%s/orgs/%s/repos" % (API, owner)
        payload = {"name": repo, "private": private,
                   "description": "ShapitoGram Android APK build pipeline"}

    status, data = request(url, token, method="POST", payload=payload)
    if status in (200, 201):
        print("репозиторий создан: %s/%s (private=%s)" % (owner, repo, private))
        return True
    detail = str(data)
    if status == 403 and "not accessible" in detail:
        print(
            "не смог создать репозиторий: HTTP 403 — так отвечает fine-grained токен.\n"
            "  Fine-grained PAT не умеет создавать репозитории: его права выдаются\n"
            "  на конкретные репозитории, а нового ещё нет.\n"
            "    1. Создай пустой репозиторий в браузере (БЕЗ README и .gitignore):\n"
            "       https://github.com/new?name=%s&visibility=public\n"
            "    2. Добавь его в доступ токена: https://github.com/settings/tokens\n"
            "       -> токен -> Repository access -> добавить %s.\n"
            "  Либо возьми classic PAT со scope `repo` — тогда --create сработает сам."
            % (repo, repo))
        return False
    print("не смог создать репозиторий: HTTP %s %s" % (status, detail[:300]))
    return False


def head_commit(token, owner, repo, branch):
    status, data = request(
        "%s/repos/%s/%s/git/ref/heads/%s" % (API, owner, repo, branch), token)
    if status == 200 and isinstance(data, dict):
        return data.get("object", {}).get("sha")
    return None


def seed_empty_repo(token, owner, repo, branch):
    """Создать первый коммит в пустом репозитории.

    Зачем это нужно
    ---------------
    `POST /git/blobs` на репозитории без коммитов отвечает
    `409 Git Repository is empty`: блоб некуда положить, пока нет дерева, а дерева
    нет, пока нет коммита. Круг замкнут. Contents API этот случай умеет — он
    создаёт файл И ветку одним запросом, поэтому первым кладём им ровно один
    маленький файл (`.gitattributes`), а дальше идём обычным путём.

    Тот же путь спасает и когда репозиторий создан через `auto_init`: там
    родитель уже есть, и функция не вызывается.
    """
    payload = {
        "message": "ShapitoGram Android pipeline: инициализация репозитория",
        "content": base64.b64encode(GITATTRIBUTES.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    status, data = request(
        "%s/repos/%s/%s/contents/.gitattributes" % (API, owner, repo), token,
        method="PUT", payload=payload)
    if status in (200, 201):
        return True
    print("не смог создать первый коммит: HTTP %s %s" % (status, str(data)[:300]))
    return False


def base_tree_sha(token, owner, repo, commit_sha):
    status, data = request(
        "%s/repos/%s/%s/git/commits/%s" % (API, owner, repo, commit_sha), token)
    if status == 200 and isinstance(data, dict):
        return data.get("tree", {}).get("sha")
    return None


def create_blob(token, owner, repo, rel, payload, attempts=6):
    """Создать блоб, переживая вторичный rate limit.

    У GitHub два разных лимита. Основной (5000 запросов в час) мы не задеваем —
    217 запросов. Вторичный — это про всплески: он приходит как `403 You have
    exceeded a secondary rate limit` и отпускает через секунды-десятки секунд.
    Без повторов половина файлов не уезжает, и разбираться в этом приходится
    вручную, поэтому здесь экспоненциальная пауза, а не отказ.
    """
    delay = 2.0
    last_error = None
    for attempt in range(attempts):
        status, data = request(
            "%s/repos/%s/%s/git/blobs" % (API, owner, repo), token, method="POST",
            payload={"content": base64.b64encode(payload).decode("ascii"),
                     "encoding": "base64"})
        if status in (200, 201) and isinstance(data, dict) and data.get("sha"):
            return rel, data["sha"], None
        detail = str(data)
        last_error = "HTTP %s %s" % (status, detail[:200])
        retryable = (status in (403, 429, 500, 502, 503, 504)
                     and ("rate limit" in detail.lower() or status != 403))
        if not retryable or attempt == attempts - 1:
            break
        # Разброс, чтобы потоки не били в лимит синхронно после общей паузы.
        time.sleep(delay + random.uniform(0, delay / 2))
        delay = min(delay * 2, 60)
    return rel, None, last_error


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token")
    parser.add_argument("--owner", default=OWNER)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--create", action="store_true", help="создать репозиторий, если нет")
    parser.add_argument("--public", action="store_true", help="созданный репозиторий публичный")
    parser.add_argument("--list", action="store_true", help="показать, что уедет")
    parser.add_argument("--dry-run", action="store_true",
                        help="собрать и посчитать, но ничего не отправлять")
    parser.add_argument("--workers", type=int, default=3,
                        help="сколько блобов создавать параллельно "
                             "(больше 4 GitHub встречает вторичным rate limit)")
    parser.add_argument("--message", default=None, help="сообщение коммита")
    args = parser.parse_args()

    manifest = build_manifest()
    total = sum(len(payload) for _, _, payload in manifest)

    if args.list:
        for rel, mode, payload in manifest:
            print("%s %8d  %s" % (mode, len(payload), rel))
        print("\nвсего %d файлов, %.2f МБ" % (len(manifest), total / 1024 / 1024))
        return 0

    if args.dry_run:
        print("файлов: %d, суммарно %.2f МБ" % (len(manifest), total / 1024 / 1024))
        print("самые крупные:")
        for rel, _, payload in sorted(manifest, key=lambda m: -len(m[2]))[:5]:
            print("  %8d  %s" % (len(payload), rel))
        print("текстовых (CRLF -> LF): %d"
              % sum(1 for rel, _, _ in manifest
                    if pathlib.Path(rel).suffix.lower() in TEXT_SUFFIXES))
        return 0

    token = resolve_token(args.token)
    print("репозиторий: %s/%s @ %s" % (args.owner, args.repo, args.branch))
    require_scopes(token)

    if args.create and not ensure_repo(token, args.owner, args.repo, not args.public):
        return 1

    parent = head_commit(token, args.owner, args.repo, args.branch)
    if parent:
        print("верхний коммит: %s" % parent[:10])
    else:
        print("ветки %s ещё нет — это будет первый коммит" % args.branch)
        if not seed_empty_repo(token, args.owner, args.repo, args.branch):
            return 1
        parent = head_commit(token, args.owner, args.repo, args.branch)
        if not parent:
            print("первый коммит создан, но ветка всё ещё не видна — попробуй ещё раз")
            return 1
        print("репозиторий засеян, коммит: %s" % parent[:10])

    # Блобы. Параллельно: 226 последовательных POST'ов — это несколько минут
    # ожидания на ровном месте, а запросы полностью независимы.
    print("\nсоздаю блобы: %d файлов, %.2f МБ, %d потоков"
          % (len(manifest), total / 1024 / 1024, args.workers))
    shas = {}
    failed = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(create_blob, token, args.owner, args.repo, rel, payload)
                   for rel, _, payload in manifest]
        for future in concurrent.futures.as_completed(futures):
            rel, sha, error = future.result()
            done += 1
            if error:
                failed.append((rel, error))
                print("  FAIL %s: %s" % (rel, error))
            else:
                shas[rel] = sha
            if done % 25 == 0 or done == len(manifest):
                print("  %d/%d" % (done, len(manifest)))

    if failed:
        print("\nне удалось создать %d блобов — коммит не делаю" % len(failed))
        for rel, error in failed[:10]:
            print("  %s: %s" % (rel, error))
        return 1

    # base_tree — дерево верхнего коммита. Без него каждый коммит был бы ПОЛНЫМ
    # деревом репозитория, и первый же прогон стёр бы всё, чего нет в конвейере
    # (например, README, созданный GitHub при создании репозитория).
    base_tree = base_tree_sha(token, args.owner, args.repo, parent) if parent else None
    print("\nсобираю дерево (base_tree=%s)..." % (base_tree[:10] if base_tree else "нет"))

    tree = [{"path": rel, "mode": mode, "type": "blob", "sha": shas[rel]}
            for rel, mode, _ in manifest]
    status, data = request("%s/repos/%s/%s/git/trees" % (API, args.owner, args.repo),
                           token, method="POST",
                           payload={"tree": tree, **({"base_tree": base_tree} if base_tree else {})})
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("sha"):
        print("не смог собрать дерево: HTTP %s %s" % (status, str(data)[:300]))
        return 1
    tree_sha = data["sha"]
    print("дерево: %s" % tree_sha[:10])

    message = args.message or (
        "ShapitoGram Android pipeline: %d файлов, %.2f МБ"
        % (len(manifest), total / 1024 / 1024))
    commit_payload = {"message": message, "tree": tree_sha}
    if parent:
        commit_payload["parents"] = [parent]
    status, data = request("%s/repos/%s/%s/git/commits" % (API, args.owner, args.repo),
                           token, method="POST", payload=commit_payload)
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("sha"):
        print("не смог создать коммит: HTTP %s %s" % (status, str(data)[:300]))
        return 1
    commit_sha = data["sha"]
    print("коммит: %s" % commit_sha[:10])

    if parent:
        status, data = request(
            "%s/repos/%s/%s/git/refs/heads/%s" % (API, args.owner, args.repo, args.branch),
            token, method="PATCH", payload={"sha": commit_sha, "force": False})
    else:
        status, data = request("%s/repos/%s/%s/git/refs" % (API, args.owner, args.repo),
                               token, method="POST",
                               payload={"ref": "refs/heads/%s" % args.branch,
                                        "sha": commit_sha})
    if status not in (200, 201):
        print("не смог обновить ветку %s: HTTP %s %s"
              % (args.branch, status, str(data)[:300]))
        return 1

    print("\nзалито: %s/%s @ %s, коммит %s"
          % (args.owner, args.repo, args.branch, commit_sha[:10]))
    print("https://github.com/%s/%s" % (args.owner, args.repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
