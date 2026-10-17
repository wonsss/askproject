#!/bin/bash
# 이 맥에서 릴리스(GitHub Actions 없이): 점검 → 버전 올리기·태그·push → 빌드·서명·공증·DMG → appcast → GitHub Release → 받은 파일 확인.
# CI(release.yml)가 하는 일과 같다. Actions가 결제·장애로 안 돌 때 쓴다.
#   make release-local                 # patch (0.1.3 → 0.1.4). V=minor · V=major · V=1.2.3
#   make release-local-current         # 태그는 이미 push됐는데 Release가 없을 때(CI 실패) — 지금 버전으로 올리기만
#   make release-check                 # 점검만 (아무것도 바꾸지 않음)
# 처음 한 번 준비할 것(docs/packaging.md '이 맥에서 릴리스'):
#   Developer ID Application 인증서(Keychain) · 공증 프로필(xcrun notarytool store-credentials askproject …)
#   Sparkle 개인 키(Keychain, build/cache/Sparkle/bin/generate_keys) · gh auth login
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-patch}"           # patch | minor | major | X.Y.Z | --current | --check
NOTARY_PROFILE="${NOTARY_PROFILE:-askproject}"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
die() { echo "❌ $*" >&2; exit 1; }
ok() { echo "✓ $*"; }

preflight() {
  echo "── 점검"
  [ "$(git branch --show-current)" = "main" ] || die "main 브랜치에서 실행하세요"
  [ -z "$(git status --porcelain)" ] || die "커밋하지 않은 변경이 있어요 — 먼저 커밋하거나 치우세요"
  [ -n "$REPO" ] || die "gh가 로그인돼 있지 않아요 — gh auth login"
  ok "GitHub $REPO"
  security find-identity -v -p codesigning | grep -q "Developer ID Application" \
    || die "Developer ID Application 인증서가 Keychain에 없어요 (docs/packaging.md 서명·공증)"
  ok "Developer ID 인증서"
  xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
    || die "공증 프로필 '$NOTARY_PROFILE'이 없어요 — xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <Apple ID> --team-id <팀 ID>"
  ok "공증 프로필 $NOTARY_PROFILE"
  bash scripts/fetch-sparkle.sh build/cache/Sparkle >/dev/null
  local want have
  want="${SU_PUBLIC_ED_KEY:-$(sed -n 's/^SU_PUBLIC_ED_KEY="\${SU_PUBLIC_ED_KEY:-\(.*\)}"$/\1/p' scripts/build-app.sh)}"
  have="$(build/cache/Sparkle/bin/generate_keys -p 2>/dev/null | tail -1 || true)"
  [ -n "$have" ] || die "Sparkle 개인 키가 Keychain에 없어요 — 앱 업데이트 서명에 필요합니다 (docs/packaging.md Sparkle 키)"
  [ "$have" = "$want" ] || die "Keychain의 Sparkle 키($have)가 앱에 넣는 공개 키($want)와 달라요 — 설치된 앱이 업데이트를 거부합니다"
  ok "Sparkle 서명 키"
  command -v rg >/dev/null || die "ripgrep이 필요해요 — brew install ripgrep"
  uv run pytest -q >/dev/null || die "테스트가 실패했어요 — uv run pytest"
  ok "테스트"
}

publish() {
  local version tag dmg updates sum tmp
  version="$(python3 scripts/bump_version.py --check)"
  tag="v$version"
  [ "$(git rev-parse HEAD)" = "$(git rev-parse -q --verify "refs/tags/$tag^{commit}" || true)" ] \
    || die "HEAD가 태그 $tag가 아니에요 — make release-local로 버전부터 올리세요"
  git fetch -q origin main
  git merge-base --is-ancestor HEAD origin/main || die "태그 커밋이 origin/main에 없어요 — 먼저 push하세요"
  ! gh release view "$tag" >/dev/null 2>&1 || die "Release $tag가 이미 있어요: https://github.com/$REPO/releases/tag/$tag"

  echo "── 빌드·서명·공증·DMG ($tag, 10분 안팎)"
  NOTARY_PROFILE="$NOTARY_PROFILE" bash scripts/build-dmg.sh
  dmg="dist/AskProject-$version.dmg"
  [ -f "$dmg" ] || die "$dmg가 없어요"

  echo "── appcast (앱 자동 업데이트)"
  updates="dist/updates"
  rm -rf "$updates"   # 이 맥에 남은 옛 DMG·델타가 appcast에 섞이지 않게 이번 DMG만 넣는다
  DMG="$dmg" DOWNLOAD_URL_PREFIX="https://github.com/$REPO/releases/download/$tag/" bash scripts/make-appcast.sh
  (cd dist && shasum -a 256 "AskProject-$version.dmg" > "AskProject-$version.dmg.sha256")
  sum="$(cut -d' ' -f1 "$dmg.sha256")"

  echo "── GitHub Release"
  gh release create "$tag" "$dmg" "$updates/appcast.xml" "$dmg.sha256" \
    --title "AskProject $version" --generate-notes --verify-tag

  echo "── 올라간 파일 확인"
  tmp="$(mktemp -d)"
  gh release download "$tag" -p "AskProject-$version.dmg" -D "$tmp"
  [ "$(shasum -a 256 "$tmp/AskProject-$version.dmg" | cut -d' ' -f1)" = "$sum" ] || die "받은 DMG의 해시가 다릅니다"
  rm -rf "$tmp"
  ok "DMG 해시 일치 ($sum)"
  echo "✅ https://github.com/$REPO/releases/tag/$tag"
  if [ "$(gh repo view --json isPrivate -q .isPrivate)" = "true" ]; then
    echo "⚠️  레포가 비공개라 로그인 없이는 DMG를 받을 수 없고, 앱 자동 업데이트(appcast)도 동작하지 않아요."
  fi
}

case "$MODE" in
  --check)
    preflight
    echo "✅ 릴리스할 준비가 됐어요"
    ;;
  --current)
    preflight
    publish
    ;;
  *)
    preflight
    # 버전 올리기 → 커밋 → 태그 → push. Actions가 돌고 있으면 태그 push로 CI도 시작한다 — 그럴 땐 make release를 쓴다
    # (둘 다 돌면 먼저 끝난 쪽이 Release를 만들고 나머지는 'Release가 이미 있음'으로 멈춘다)
    bash scripts/release.sh "$MODE" -y
    publish
    ;;
esac
