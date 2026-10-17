#!/bin/bash
# Sparkle appcast 생성: DMG로 appcast.xml을 만들고 EdDSA로 서명한다.
#   bash scripts/make-appcast.sh                     # 이 맥: 개인 키는 Keychain에서
#   SPARKLE_KEY_FILE=key.txt DOWNLOAD_URL_PREFIX=https://…/ bash scripts/make-appcast.sh   # CI
# 결과 dist/updates/appcast.xml과 DMG를 SU_FEED_URL이 가리키는 곳에 올린다(인증 없이 받을 수 있어야 함).
# 서명 키는 처음 한 번 만든다(개인 키는 이 맥의 Keychain, 공개 키는 build-app.sh 기본값):
#   build/cache/Sparkle/bin/generate_keys
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPARKLE="$REPO_ROOT/build/cache/Sparkle"
UPDATES="$REPO_ROOT/dist/updates"
bash "$REPO_ROOT/scripts/fetch-sparkle.sh" "$SPARKLE"
mkdir -p "$UPDATES"
# DMG=dist/AskProject-1.2.3.dmg를 주면 그 파일만 넣는다(이 맥에 남은 옛 빌드가 appcast에 섞이지 않게)
if [ -n "${DMG:-}" ]; then cp "$DMG" "$UPDATES/"; else cp "$REPO_ROOT"/dist/AskProject-*.dmg "$UPDATES/"; fi
args=()
[ -n "${SPARKLE_KEY_FILE:-}" ] && args+=(--ed-key-file "$SPARKLE_KEY_FILE")
[ -n "${DOWNLOAD_URL_PREFIX:-}" ] && args+=(--download-url-prefix "$DOWNLOAD_URL_PREFIX")
"$SPARKLE/bin/generate_appcast" ${args[@]+"${args[@]}"} "$UPDATES"
echo "appcast: $UPDATES/appcast.xml"
