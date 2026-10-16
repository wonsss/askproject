#!/bin/bash
# 앱 번들의 모든 Mach-O를 안쪽부터 Developer ID + Hardened Runtime으로 서명한다(공증 요건).
#   SIGN_ID="Developer ID Application: …" bash scripts/sign-app.sh dist/AskProject.app
# claude 바이너리는 Anthropic의 Developer ID 서명(Hardened Runtime·allow-jit 등 entitlement 포함)을 그대로 둔다.
# 다시 서명하면 우리 entitlement로 바뀌어 Bun 런타임이 실행 중 JIT 메모리를 못 쓴다.
# cloudflared도 Cloudflare의 Developer ID 서명(Hardened Runtime·타임스탬프)을 그대로 두고 확인만 한다.
set -euo pipefail

APP="${1:?앱 경로}"
SIGN_ID="${SIGN_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)}"
[ -n "$SIGN_ID" ] || { echo "Developer ID Application 인증서가 없습니다 (SIGN_ID로 지정)"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ENT="$HERE/../macos/entitlements"
CLAUDE_REL="Contents/Resources/backend/python/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude"
CFD_REL="Contents/Resources/backend/bin/cloudflared"

sign() { codesign --force --timestamp --options runtime --sign "$SIGN_ID" "$@"; }

# claude: 우리가 서명하지 않는다. Anthropic Developer ID 서명인지 확인만
codesign --verify --strict "$APP/$CLAUDE_REL"
claude_sig="$(codesign -dvv "$APP/$CLAUDE_REL" 2>&1)"
case "$claude_sig" in
  *"Authority=Developer ID Application: Anthropic PBC"*"TeamIdentifier=Q6L2SF6YDW"*) ;;
  *) echo "claude 바이너리 서명이 Anthropic Developer ID가 아닙니다"; exit 1 ;;
esac

codesign --verify --strict "$APP/$CFD_REL"
case "$(codesign -dvv "$APP/$CFD_REL" 2>&1)" in
  *"Authority=Developer ID Application: Cloudflare Inc."*"TeamIdentifier=68WVV388M8"*) ;;
  *) echo "cloudflared 서명이 Cloudflare Developer ID가 아닙니다"; exit 1 ;;
esac

# 1) 백엔드 라이브러리·실행 파일 (claude·cloudflared 제외). 확장자가 없는 Mach-O도 file로 찾는다
find "$APP/Contents/Resources/backend" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) \
  ! -path "*/$(basename "$CLAUDE_REL")" ! -path "*/$CFD_REL" -print0 \
  | xargs -0 file | grep 'Mach-O' | cut -d: -f1 \
  | tr '\n' '\0' | xargs -0 -n 40 -P 6 codesign --force --timestamp --options runtime --sign "$SIGN_ID" >/dev/null
# 파이썬 인터프리터는 entitlement 없이 서명한다(arm64 libffi는 정적 트램펄린이라 JIT 메모리 불필요 — W0에서 확인)

# 2) 프레임워크(Sparkle 등) → 런처 → 앱
if [ -d "$APP/Contents/Frameworks" ]; then
  for fw in "$APP"/Contents/Frameworks/*.framework; do
    [ -e "$fw" ] || continue
    # Sparkle은 안쪽 XPC 서비스·Autoupdate·Updater.app을 먼저 서명한다. Downloader.xpc는 네트워크 entitlement를 유지
    # Versions/Current는 심볼릭 링크라 -H로 따라간다(안 그러면 아무것도 못 찾아 공증이 거절된다)
    find -H "$fw/Versions/Current" \( -name '*.xpc' -o -name '*.app' \) -prune -print0 2>/dev/null \
      | xargs -0 -I{} codesign --force --timestamp --options runtime --preserve-metadata=entitlements --sign "$SIGN_ID" {} >/dev/null
    [ -f "$fw/Versions/Current/Autoupdate" ] && sign "$fw/Versions/Current/Autoupdate" >/dev/null
    sign "$fw" >/dev/null
  done
fi
sign "$APP/Contents/MacOS/askproject-backend" >/dev/null
sign --entitlements "$ENT/app.entitlements" "$APP" >/dev/null

codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2
echo "서명: $SIGN_ID"
