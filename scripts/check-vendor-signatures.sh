#!/bin/bash
# 다시 서명하지 않는 내장 바이너리(claude·cloudflared)가 원래 회사의 Developer ID 서명 그대로인지 확인한다.
#   bash scripts/check-vendor-signatures.sh dist/AskProject.app
# sign-app.sh가 서명 전에 부르고, CI(package.yml)는 서명 없는 빌드에서 부른다 — 공증 전에 깨진 번들을 잡는다.
set -euo pipefail
APP="${1:?앱 경로}"
BACKEND="$APP/Contents/Resources/backend"
fail=0

check() {  # 이름, 경로, 서명 주체, 팀 ID
  local name="$1" path="$2" authority="$3" team="$4" sig
  if [ ! -f "$path" ]; then echo "❌ $name 없음: $path"; fail=1; return; fi
  if ! codesign --verify --strict "$path" 2>/dev/null; then echo "❌ $name 서명 검증 실패"; fail=1; return; fi
  sig="$(codesign -dvv "$path" 2>&1)"
  case "$sig" in
    *"Authority=Developer ID Application: $authority"*"TeamIdentifier=$team"*) ;;
    *) echo "❌ $name 서명이 $authority Developer ID($team)가 아닙니다"; fail=1; return ;;
  esac
  case "$sig" in
    *"(runtime)"*) echo "✅ $name — $authority ($team), Hardened Runtime" ;;
    *) echo "❌ $name에 Hardened Runtime이 없습니다(공증 거절)"; fail=1 ;;
  esac
}

check claude "$BACKEND/python/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude" "Anthropic PBC" Q6L2SF6YDW
check cloudflared "$BACKEND/bin/cloudflared" "Cloudflare Inc." 68WVV388M8
exit "$fail"
