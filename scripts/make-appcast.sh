#!/bin/bash
# Sparkle appcast 생성: dist/updates/의 DMG들로 appcast.xml을 만들고 EdDSA로 서명한다.
#   bash scripts/make-appcast.sh             → dist/updates/appcast.xml
# 그다음 dist/updates/ 전체를 인증 없이 받을 수 있는 곳(R2·S3 공개 버킷 등)에 올린다. 그 주소가 SU_FEED_URL.
# 서명 키는 처음 한 번 만든다(개인 키는 이 맥의 Keychain에 저장, 공개 키를 SU_PUBLIC_ED_KEY로):
#   build/cache/Sparkle/bin/generate_keys
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPARKLE="$REPO_ROOT/build/cache/Sparkle"
UPDATES="$REPO_ROOT/dist/updates"
bash "$REPO_ROOT/scripts/fetch-sparkle.sh" "$SPARKLE"
mkdir -p "$UPDATES"
cp "$REPO_ROOT"/dist/AskProject-*.dmg "$UPDATES/" 2>/dev/null || true
"$SPARKLE/bin/generate_appcast" "$UPDATES"
echo "appcast: $UPDATES/appcast.xml — dist/updates/ 를 SU_FEED_URL의 폴더에 올리세요"
