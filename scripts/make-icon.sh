#!/bin/bash
# 앱 아이콘(macos/AskProjectBar/AppIcon.icns)을 scripts/make-icon.swift로 다시 만든다.
# 결과 icns는 커밋한다 — 빌드 스크립트는 이 파일을 복사만 한다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

xcrun swift "$REPO_ROOT/scripts/make-icon.swift" "$TMP/AppIcon.iconset"
iconutil -c icns "$TMP/AppIcon.iconset" -o "$REPO_ROOT/macos/AskProjectBar/AppIcon.icns"
echo "아이콘: macos/AskProjectBar/AppIcon.icns"
