#!/bin/bash
# 배포용 AskProject.app 조립: SwiftUI 앱 + 런처 + launchd 에이전트 plist + 내장 백엔드 → 서명.
#   bash scripts/build-app.sh                 # dist/AskProject.app (Developer ID 서명)
#   SIGN=0 bash scripts/build-app.sh          # 서명 생략(ad-hoc) → dist/unsigned/AskProject.app — 이 맥·CI에서 빠르게 확인할 때
#                                             # (서명 앱 dist/AskProject.app을 덮어쓰지 않게 폴더를 나눈다)
#   REBUILD_BACKEND=1 bash scripts/build-app.sh
# 개발용(uv run으로 레포를 부르는) 앱은 scripts/build-macos-app.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/macos/AskProjectBar"
BACKEND="$REPO_ROOT/build/backend"
if [ "${SIGN:-1}" = "1" ]; then APP="$REPO_ROOT/dist/AskProject.app"; else APP="$REPO_ROOT/dist/unsigned/AskProject.app"; fi
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
BUILD_NO="${BUILD_NO:-$(date '+%Y%m%d%H%M')}"
SPARKLE="$REPO_ROOT/build/cache/Sparkle"

if [ "${REBUILD_BACKEND:-0}" = "1" ] || [ ! -f "$BACKEND/BUILD_INFO.json" ]; then
  bash "$REPO_ROOT/scripts/build-backend-bundle.sh" "$BACKEND"
fi
bash "$REPO_ROOT/scripts/fetch-sparkle.sh" "$SPARKLE"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Library/LaunchAgents" "$APP/Contents/Frameworks"

cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
cp "$SRC/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" -c "Set :CFBundleVersion $BUILD_NO" \
  -c "Delete :AskProjectRepo" -c "Delete :AskProjectUV" "$APP/Contents/Info.plist"
[ -n "${SU_FEED_URL:-}" ] && /usr/libexec/PlistBuddy -c "Set :SUFeedURL $SU_FEED_URL" "$APP/Contents/Info.plist"
# Sparkle 공개 키(개인 키는 이 맥의 Keychain, docs/packaging.md). SU_FEED_URL이 없으면 업데이트는 꺼진 채로 빌드된다
SU_PUBLIC_ED_KEY="${SU_PUBLIC_ED_KEY:-MCkJ16w/gJiBXaK9B95WSFfkWtpvnOY2dTDFo5Vn+3Q=}"
/usr/libexec/PlistBuddy -c "Set :SUPublicEDKey $SU_PUBLIC_ED_KEY" "$APP/Contents/Info.plist"

ditto "$SPARKLE/Sparkle.framework" "$APP/Contents/Frameworks/Sparkle.framework"
xcrun swiftc \
  -sdk "$(xcrun --sdk macosx --show-sdk-path)" -target arm64-apple-macos13.0 -swift-version 5 -parse-as-library -O \
  -F "$SPARKLE" -framework Sparkle -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
  -framework SwiftUI -framework AppKit -framework UserNotifications -framework ServiceManagement -framework Security \
  "$SRC"/Sources/*.swift -o "$APP/Contents/MacOS/AskProjectBar"
clang -O2 -Wall -arch arm64 -mmacosx-version-min=13.0 \
  -o "$APP/Contents/MacOS/askproject-backend" "$REPO_ROOT/macos/launcher/askproject-backend.c"
cp "$REPO_ROOT"/macos/LaunchAgents/*.plist "$APP/Contents/Library/LaunchAgents/"
ditto "$BACKEND" "$APP/Contents/Resources/backend"

bash "$REPO_ROOT/scripts/collect-licenses.sh" "$APP/Contents/Resources/backend" "$APP/Contents/Resources/THIRD_PARTY_NOTICES.txt"
bash "$REPO_ROOT/scripts/check-bundle.sh" "$APP"

if [ "${SIGN:-1}" = "1" ]; then
  bash "$REPO_ROOT/scripts/sign-app.sh" "$APP"
else
  codesign --force --deep --sign - "$APP" >/dev/null
  bash "$REPO_ROOT/scripts/check-vendor-signatures.sh" "$APP"  # --deep이 내장 바이너리 서명을 덮지 않았는지
  echo "ad-hoc 서명 (배포 불가)"
fi
echo "앱: $APP ($(du -sh "$APP" | cut -f1)) · v$VERSION ($BUILD_NO)"
