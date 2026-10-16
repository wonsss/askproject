#!/bin/bash
# 배포 산출물 검사: 회사별 설정·평가 문항·비밀값·런타임 데이터가 들어가지 않았는지, 실행 중 쓰기 흔적이 없는지.
set -euo pipefail
APP="${1:?앱 경로}"
fail=0
check() {  # 설명, find 조건...
  local what="$1"; shift
  local hits; hits="$(find "$APP" "$@" -print 2>/dev/null | head -5)"
  if [ -n "$hits" ]; then echo "❌ $what:"; echo "$hits"; fail=1; fi
}
check "평가 문항·결과" \( -name 'golden.yaml' -o -path '*/eval/results/*' \)
check "비밀값·키 파일" \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.p8' -o -path '*/secrets/*' \) ! -path '*/certifi/cacert.pem'
check "런타임 데이터" \( -name '*.sqlite3' -o -name '*.sqlite3-wal' -o -name 'serve.lock' \)
check "회사별 설정 YAML" \( -name 'repos.yaml' -o -name 'domains.yaml' -o -name 'glossary.yaml' \) ! -path '*/askproject/defaults/*'
# 기본값 템플릿에 레포가 채워져 있으면 안 된다
if grep -Eq '^\s*-\s*name:' "$APP"/Contents/Resources/backend/python/lib/python3.12/site-packages/askproject/defaults/repos.yaml 2>/dev/null; then
  echo "❌ 기본 repos.yaml에 레포 항목이 있습니다"; fail=1
fi
[ "$fail" = 0 ] && echo "✅ 번들 검사 통과 (내부 설정·비밀값·런타임 데이터 없음)"
exit "$fail"
