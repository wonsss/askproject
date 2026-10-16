#!/bin/bash
# 배포 DMG: 앱 조립·서명 → 공증(notarytool) → 스테이플 → DMG 서명·공증·스테이플.
#   NOTARY_PROFILE=askproject bash scripts/build-dmg.sh          # 이 맥: Keychain에 저장한 공증 프로필
#   NOTARY_KEY=AuthKey.p8 NOTARY_KEY_ID=… NOTARY_ISSUER=… bash scripts/build-dmg.sh   # CI: App Store Connect API 키
# 공증 프로필은 한 번만 저장한다(앱 암호는 사용자가 직접 입력, 이 스크립트는 다루지 않는다):
#   xcrun notarytool store-credentials askproject --apple-id <Apple ID> --team-id AE57B3B397
# Sparkle 배포까지 하려면 SU_FEED_URL·SU_PUBLIC_ED_KEY를 함께 넘기고, 끝난 뒤 scripts/make-appcast.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO_ROOT/dist/AskProject.app"
if [ -n "${NOTARY_KEY:-}" ]; then
  NOTARY_AUTH=(--key "$NOTARY_KEY" --key-id "${NOTARY_KEY_ID:?NOTARY_KEY_ID가 필요합니다}" --issuer "${NOTARY_ISSUER:?NOTARY_ISSUER가 필요합니다}")
else
  NOTARY_AUTH=(--keychain-profile "${NOTARY_PROFILE:?NOTARY_PROFILE(저장한 공증 프로필) 또는 NOTARY_KEY(API 키)가 필요합니다}")
fi
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
DMG="$REPO_ROOT/dist/AskProject-$VERSION.dmg"
SIGN_ID="${SIGN_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)}"

[ "${SKIP_BUILD:-0}" = "1" ] || SIGN=1 bash "$REPO_ROOT/scripts/build-app.sh"

notarize() {  # 제출 → 결과 대기 → 실패면 로그 출력
  local out id
  out="$(xcrun notarytool submit "$1" "${NOTARY_AUTH[@]}" --wait --output-format json)"
  id="$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  if ! echo "$out" | grep -q '"status" *: *"Accepted"'; then
    echo "공증 실패 ($id):"; xcrun notarytool log "$id" "${NOTARY_AUTH[@]}"; exit 1
  fi
  echo "공증 통과: $id"
}

# 1) 앱 공증 (zip으로 제출) → 스테이플
ZIP="$REPO_ROOT/dist/AskProject-notarize.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
notarize "$ZIP"
rm -f "$ZIP"
xcrun stapler staple "$APP"

# 2) DMG: 앱 + 응용 프로그램 폴더 바로 가기
STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/AskProject.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "AskProject" -srcfolder "$STAGE" -fs HFS+ -format ULFO -ov "$DMG" >/dev/null
rm -rf "$STAGE"
codesign --force --timestamp --sign "$SIGN_ID" "$DMG"
notarize "$DMG"
xcrun stapler staple "$DMG"

# 3) 확인: Gatekeeper가 다운로드한 것처럼 평가
spctl -a -vv -t exec "$APP"
spctl -a -vv -t open --context context:primary-signature "$DMG"
echo "DMG: $DMG ($(du -h "$DMG" | cut -f1))"
