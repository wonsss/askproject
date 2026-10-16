#!/bin/bash
# 릴리스 한 줄: 버전 올리기 → 커밋 → 태그 → push. 빌드·서명·공증·GitHub Release는 CI(release.yml)가 한다.
#   make release                       # patch (0.1.0 → 0.1.1) = bash scripts/release.sh
#   make release-minor · make release-major · make release V=1.4.2
#   bash scripts/release.sh patch -y   # 확인 질문 없이
# 같은 일을 GitHub 화면에서 하려면: Actions → release → Run workflow
set -euo pipefail

cd "$(dirname "$0")/.."
BUMP="${1:-patch}"
YES="${2:-}"
die() { echo "❌ $*" >&2; exit 1; }

[ "$(git branch --show-current)" = "main" ] || die "main 브랜치에서 실행하세요"
[ -z "$(git status --porcelain)" ] || die "커밋하지 않은 변경이 있어요 — 먼저 커밋하거나 치우세요"
git fetch -q origin main --tags
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || die "origin/main과 달라요 — git pull 또는 git push 먼저"

CURRENT="$(python3 scripts/bump_version.py --check)"
NEW="$(python3 scripts/bump_version.py "$BUMP")"
TAG="v$NEW"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  git checkout -q -- .
  die "태그 $TAG가 이미 있어요"
fi

echo "릴리스: $CURRENT → $NEW ($TAG)"
git --no-pager diff --stat
if [ "$YES" != "-y" ]; then
  read -r -p "커밋·태그·push할까요? [y/N] " ok
  [ "$ok" = "y" ] || { git checkout -q -- .; echo "취소했어요(변경 되돌림)"; exit 1; }
fi

git add pyproject.toml uv.lock src/askproject/__init__.py macos/AskProjectBar/Info.plist
git commit -q -m "release: $TAG"
git tag -a "$TAG" -m "AskProject $NEW"
git push -q --atomic origin main "$TAG"   # 둘 다 올라가거나 둘 다 안 올라간다
repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo '')"
echo "✅ $TAG push 완료 — CI가 DMG를 빌드·공증해 Release에 올려요 (10~15분)"
[ -n "$repo" ] && echo "   진행 상황: https://github.com/$repo/actions/workflows/release.yml"
