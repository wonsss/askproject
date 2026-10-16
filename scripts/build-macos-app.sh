#!/bin/bash
# AskProject 메뉴 막대 앱 빌드·설치.
#   bash scripts/build-macos-app.sh            # dist/dev/AskProject.app 빌드 → ~/Applications/AskProject.app 설치·실행
#   NO_INSTALL=1 bash scripts/build-macos-app.sh   # 빌드만
# 요구: macOS 13+, Xcode Command Line Tools(xcrun swiftc), uv
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/macos/AskProjectBar"
APP="$REPO_ROOT/dist/dev/AskProject.app"   # 배포 앱(scripts/build-app.sh)은 dist/AskProject.app
EXE="$APP/Contents/MacOS/AskProjectBar"
ARCH="${ARCH:-$(uname -m)}"          # arm64 | x86_64
UV_PATH="$(command -v uv || echo /opt/homebrew/bin/uv)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :AskProjectRepo $REPO_ROOT" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :AskProjectUV $UV_PATH" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $(date '+%Y%m%d%H%M')" "$APP/Contents/Info.plist"

xcrun swiftc \
  -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
  -target "$ARCH-apple-macos13.0" \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -framework SwiftUI -framework AppKit -framework UserNotifications -framework ServiceManagement \
  "$SRC"/Sources/*.swift \
  -o "$EXE"

# 알림 권한 등에 번들 서명이 필요하다. 배포용이 아니라 이 맥에서만 쓰므로 ad-hoc 서명.
codesign --force --sign - "$APP" >/dev/null
echo "빌드: $APP (레포 $REPO_ROOT, uv $UV_PATH)"

if [ "${NO_INSTALL:-0}" = "1" ]; then
  exit 0
fi

mkdir -p "$HOME/Applications"
pkill -x AskProjectBar 2>/dev/null || true
rm -rf "$HOME/Applications/AskProject.app"
cp -R "$APP" "$HOME/Applications/AskProject.app"
open "$HOME/Applications/AskProject.app"
echo "설치·실행: ~/Applications/AskProject.app (메뉴 막대 아이콘을 누르세요)"
