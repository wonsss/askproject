#!/bin/bash
# 내장 구성 요소의 라이선스 고지를 한 파일로 모은다(파이썬 패키지·인터프리터·ripgrep·cloudflared·claude 약관 안내).
set -euo pipefail
BACKEND="${1:?백엔드 폴더}"
OUT="${2:?출력 파일}"
SITE="$BACKEND/python/lib/python3.12/site-packages"
{
  echo "AskProject — 제3자 구성 요소 고지"
  echo
  echo "== Claude Code CLI (claude_agent_sdk/_bundled/claude) =="
  echo "Anthropic PBC 소유. claude-agent-sdk 휠에 포함된 그대로 재배포하며, 사용은 Anthropic 약관"
  echo "(https://www.anthropic.com/legal/commercial-terms)을 따른다."
  echo
  echo "== CPython $(cat "$BACKEND/BUILD_INFO.json" | sed -n 's/.*"python": "\(.*\)".*/\1/p') (python-build-standalone) =="
  cat "$BACKEND/python/lib/python3.12/LICENSE.txt" 2>/dev/null || echo "PSF License"
  echo
  echo "== ripgrep =="
  cat "$BACKEND/licenses/ripgrep/LICENSE-MIT"
  echo
  echo "== cloudflared $(sed -n 's/.*"cloudflared": "\(.*\)".*/\1/p' "$BACKEND/BUILD_INFO.json") (Cloudflare, Apache-2.0) =="
  cat "$BACKEND/licenses/cloudflared/LICENSE"
  for d in "$SITE"/*.dist-info; do
    name="$(sed -n 's/^Name: //p' "$d/METADATA" | head -1)"
    ver="$(sed -n 's/^Version: //p' "$d/METADATA" | head -1)"
    lic="$(sed -n 's/^License-Expression: //p;s/^License: //p' "$d/METADATA" | head -1)"
    echo
    echo "== $name $ver${lic:+ ($lic)} =="
    for f in $(find "$d" -type f \( -iname 'LICEN[CS]E*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) 2>/dev/null); do
      cat "$f"; echo
    done
  done
} > "$OUT"
