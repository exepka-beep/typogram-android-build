# Android-конвейер TypoGram

Собирает подписанный release-APK **TypoGram** (`com.typogram.messenger`) на
GitHub Actions из форка `owpengram-android-client`. Клиент подключается к
серверу `typogram.duckdns.org:2398`.

Конвейер сделан из конвейера ShapitoGram, отличия:

| Что | Значение |
|---|---|
| имя / пакет | `TypoGram` / `com.typogram.messenger` |
| адрес сервера | `typogram.duckdns.org:2398` (домен, не IP) |
| RSA-ключ сервера | `config/server-rsa-public.pem` (PKCS#1, fingerprint `0x2f8f…`) |
| иконки | родные, Telegram: брендовая графика из overlay удалена |
| `server-info` | не используется — эндпоинта на VPS нет, ключ лежит в репозитории |

## Домен вместо IP

В ShapitoGram `DEFAULT_HOST` обязан был быть числовым IPv4: нативный слой зовёт
`inet_pton` и имён не понимает. Здесь в overlay лежит пропатченный
`TMessagesProj/jni/tgnet/ConnectionSocket.cpp`: при провале `inet_pton` он
резолвит имя через `gethostbyname` — тем же способом, каким это уже делала ветка
прокси в том же файле. Проверяют это два места: `tools/check_pipeline.py`
локально и шаг ребрендинга в CI.

## Формат ключа

Нативный парсер — `PEM_read_bio_RSAPublicKey`, он принимает только PKCS#1:

```
-----BEGIN RSA PUBLIC KEY-----
```

SPKI (`-----BEGIN PUBLIC KEY-----`, то, что отдаёт `openssl rsa -pubout`) он не
разбирает: отпечаток получается 0, рукопожатие падает. Конвертация:

```bash
openssl rsa -pubin -in spki.pem -RSAPublicKey_out -out config/server-rsa-public.pem
```

## Как собрать

```bash
PY=C:/Users/rgali/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe

$PY tools/check_pipeline.py
$PY tools/run_build.py secrets --keystore ../artifacts/release.keystore
$PY tools/run_build.py dispatch --create --public --repo typogram-android-build
$PY tools/run_build.py watch
$PY tools/run_build.py download
```

Сборка идёт 40–70 минут. `mapping.txt` в артефактах нужен, чтобы читать
стектрейсы релизной сборки.
