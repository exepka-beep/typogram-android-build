#!/usr/bin/env bash
# Структурная проверка собранного APK. Запускается ВНУТРИ образа сборки:
# aapt2 и apksigner лежат в Android SDK, а больше здесь ничего не нужно.
#
# Зачем проверять то, что и так собирается
# ---------------------------------------
# Три ошибки из этого конвейера не видны до установки на телефон:
#   1. остался APP_PACKAGE XTree Gram -> APK ставится как чужое приложение
#      и не может обновиться поверх ShapitoGram;
#   2. подпись ушла на другой ключ -> обновление поверх установленного
#      приложения отказывается ставиться ("App not installed");
#   3. apksigner прошёл, но схема подписи v2 потеряна (классическая причина —
#      zipalign, запущенный ПОСЛЕ подписи) -> Android 11+ отказывается ставить APK.
# Все три проверяются здесь за секунды вместо цикла «собрал 50 минут — поставил —
# не работает».
#
# Использование (внутри контейнера):
#   verify_apk.sh <apk> [package] [versionCode] [versionName] [cert-SHA-256]

set -euo pipefail

APK="${1:?нужен путь к APK}"
EXPECT_PACKAGE="${2:-}"
EXPECT_VERSION_CODE="${3:-}"
EXPECT_VERSION_NAME="${4:-}"
EXPECT_CERT="${5:-}"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK  : $*"; }

[[ -f "$APK" ]] || fail "нет файла $APK"

# Берём самые свежие build-tools, а не жёстко 36.0.0: Dockerfile ставит именно
# их, но если он однажды обновится, проверка не должна ломаться на пустом месте.
BUILD_TOOLS_DIR="$(find "${ANDROID_HOME}/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)"
AAPT2="${BUILD_TOOLS_DIR}/aapt2"
APKSIGNER="${BUILD_TOOLS_DIR}/apksigner"
[[ -x "$AAPT2" ]] || fail "нет aapt2 в ${BUILD_TOOLS_DIR}"
[[ -x "$APKSIGNER" ]] || fail "нет apksigner в ${BUILD_TOOLS_DIR}"
echo "build-tools: ${BUILD_TOOLS_DIR}"
echo "APK: $(du -h "$APK" | cut -f1)  $APK"

badging="$("$AAPT2" dump badging "$APK")"
pkg_line="$(printf '%s\n' "$badging" | grep -m1 '^package:')"
[[ -n "$pkg_line" ]] || fail "aapt2 не отдал строку package:"

package="$(printf '%s\n' "$pkg_line" | sed -n "s/^package: name='\([^']*\)'.*/\1/p")"
version_code="$(printf '%s\n' "$pkg_line" | sed -n "s/^package:.*versionCode='\([^']*\)'.*/\1/p")"
version_name="$(printf '%s\n' "$pkg_line" | sed -n "s/^package:.*versionName='\([^']*\)'.*/\1/p")"
label="$(printf '%s\n' "$badging" | sed -n "s/^application-label:'\(.*\)'$/\1/p" | head -1)"
min_sdk="$(printf '%s\n' "$badging" | sed -n "s/^sdkVersion:'\([^']*\)'.*/\1/p" | head -1)"
native="$(printf '%s\n' "$badging" | grep -c "^native-code:" || true)"

echo "package     : ${package}"
echo "versionCode : ${version_code}"
echo "versionName : ${version_name}"
echo "label       : ${label}"
echo "minSdk      : ${min_sdk:-?}"
echo "native-code : $(printf '%s\n' "$badging" | sed -n "s/^native-code: *//p" | head -1)"

if [[ -n "$EXPECT_PACKAGE" && "$package" != "$EXPECT_PACKAGE" ]]; then
  fail "package '${package}', ожидали '${EXPECT_PACKAGE}' (остался APP_PACKAGE XTree Gram?)"
fi
[[ -n "$EXPECT_PACKAGE" ]] && ok "имя пакета ${package}"

if [[ -n "$EXPECT_VERSION_CODE" && "$version_code" != "$EXPECT_VERSION_CODE" ]]; then
  fail "versionCode '${version_code}', ожидали '${EXPECT_VERSION_CODE}'"
fi
[[ -n "$EXPECT_VERSION_CODE" ]] && ok "versionCode ${version_code}"

if [[ -n "$EXPECT_VERSION_NAME" && "$version_name" != "$EXPECT_VERSION_NAME" ]]; then
  fail "versionName '${version_name}', ожидали '${EXPECT_VERSION_NAME}'"
fi

# Подпись. Проверяем и сам факт проверки, и то, ЧЕМ подписано.
certs="$("$APKSIGNER" verify --print-certs "$APK" 2>&1)" \
  || fail "apksigner verify не прошёл:
$certs"

cert_sha="$(printf '%s\n' "$certs" | sed -n 's/^Signer #1 certificate SHA-256 digest: //p' | head -1)"
[[ -n "$cert_sha" ]] || fail "не нашёл отпечаток сертификата в выводе apksigner"
echo "cert SHA-256: ${cert_sha}"

if [[ -n "$EXPECT_CERT" ]]; then
  norm_actual="$(printf '%s' "$cert_sha" | tr -d ':' | tr 'A-F' 'a-f')"
  norm_expect="$(printf '%s' "$EXPECT_CERT" | tr -d ':' | tr 'A-F' 'a-f')"
  if [[ "$norm_actual" != "$norm_expect" ]]; then
    fail "APK подписан НЕ нашим ключом:
  получено  : ${cert_sha}
  ожидалось : ${EXPECT_CERT}
  С таким ключом обновление поверх установленного ShapitoGram не встанет."
  fi
  ok "подпись нашим ключом (${EXPECT_CERT:0:17}...)"
fi

# Схемы подписи. v2 обязателен для Android 11+; его отсутствие — почти всегда
# признак zipalign после подписи.
schemes="$("$APKSIGNER" verify --verbose "$APK" 2>&1)"
for scheme in v1 v2 v3; do
  value="$(printf '%s\n' "$schemes" | sed -n "s/^Verified using ${scheme} scheme.*: //p" | head -1)"
  echo "verified ${scheme}: ${value:-не заявлена}"
done
v2="$(printf '%s\n' "$schemes" | sed -n 's/^Verified using v2 scheme.*: //p' | head -1)"
if [[ "$v2" != "true" ]]; then
  fail "схема подписи v2 отсутствует — Android 11+ такой APK не установит
  (обычная причина: zipalign выполнен ПОСЛЕ apksigner)"
fi
ok "схема подписи v2 на месте"

# Хоть один ABI должен быть собран, иначе APK поставится и не запустится.
if [[ "${native}" -eq 0 ]]; then
  fail "в APK нет native-code: нативные библиотеки не собрались"
fi

echo
echo "APK прошёл проверку."
