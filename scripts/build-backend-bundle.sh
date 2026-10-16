#!/bin/bash
# 백엔드 번들: python-build-standalone 인터프리터 + 의존성 + rg + cloudflared를 한 폴더로 만든다(앱의 Contents/Resources/backend).
#   bash scripts/build-backend-bundle.sh [출력 폴더]     # 기본 build/backend
# venv 대신 PBS 인터프리터의 site-packages에 직접 설치한다 — venv의 pyvenv.cfg는 기반 인터프리터를 절대 경로로
# 가리켜 앱을 옮기면 깨진다. 결과물은 위치와 무관하게 `python/bin/python3.12 -I -B -m askproject`로 실행된다.
# 요구: uv, curl, shasum (git·Homebrew 불필요)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$REPO_ROOT/build/backend}"
WORK="$REPO_ROOT/build/work"
PY_VER="3.12"
RG_VER="15.2.0"
RG_NAME="ripgrep-$RG_VER-aarch64-apple-darwin"
# cloudflared(웹 콘솔 팀 공유). 올릴 때는 릴리스 자산의 sha256(GitHub 'digest')으로 함께 바꾼다
CFD_VER="2026.9.3"
CFD_SHA256="587c2cfb1c230fe36c7fa7727da78be459dae028cabe8c001291999350f07095"

[ "$(uname -m)" = "arm64" ] || { echo "arm64 전용 빌드입니다"; exit 1; }
rm -rf "$OUT" "$WORK"
mkdir -p "$OUT/bin" "$WORK"

# 1) 인터프리터: uv가 관리하는 PBS 설치본을 그대로 복사
uv python install "$PY_VER" >/dev/null
PY_BIN="$(uv python find --managed-python "$PY_VER")"
PY_HOME="$(cd "$(dirname "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PY_BIN")")/.." && pwd)"
cp -R "$PY_HOME" "$OUT/python"
PY="$OUT/python/bin/python$PY_VER"
SITE="$OUT/python/lib/python$PY_VER/site-packages"
rm -f "$OUT/python/lib/python$PY_VER/EXTERNALLY-MANAGED"

# 2) 의존성(uv.lock 고정) + 프로젝트 휠
(cd "$REPO_ROOT" && uv export --frozen --no-dev --no-hashes --no-emit-project -q -o "$WORK/requirements.txt")
(cd "$REPO_ROOT" && uv build --wheel -q -o "$WORK/wheel")
uv pip install -q --python "$PY" --break-system-packages --link-mode copy \
  -r "$WORK/requirements.txt" "$WORK"/wheel/askproject-*.whl

# 3) 쓰지 않는 표준 라이브러리·헤더·스크립트 정리 (bin의 진입점 스크립트는 절대 경로 shebang이라 지운다)
STD="$OUT/python/lib/python$PY_VER"
rm -rf "$OUT/python/include" "$OUT/python/share" "$OUT/python/lib/pkgconfig" \
  "$STD"/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,config-$PY_VER-darwin} \
  "$STD"/lib-dynload/_tkinter*.so "$OUT/python/lib"/{libtcl*,libtk*,tcl*,tk*,itcl*,thread*} \
  "$SITE"/pip "$SITE"/pip-*.dist-info
find "$OUT/python/bin" -mindepth 1 ! -name "python$PY_VER" ! -name python3 -delete
find "$OUT/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$SITE" -type d \( -name tests -o -name test \) -path "*/site-packages/*/*" -prune -exec rm -rf {} +

# 4) 사전 컴파일: 실행 중 __pycache__를 쓰지 않는다(번들 서명이 깨짐). unchecked-hash라 mtime 비교도 없다
"$PY" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$OUT/python/lib" >/dev/null

# 5) rg: 공식 릴리스(정적 링크, Homebrew 판은 pcre2 dylib에 묶임) + sha256 확인
RG_CACHE="$REPO_ROOT/build/cache/$RG_NAME"
if [ ! -x "$RG_CACHE/rg" ]; then
  mkdir -p "$RG_CACHE"
  base="https://github.com/BurntSushi/ripgrep/releases/download/$RG_VER/$RG_NAME.tar.gz"
  curl -fsSL "$base" -o "$RG_CACHE/rg.tar.gz"
  want="$(curl -fsSL "$base.sha256" | awk '{print $1}')"
  got="$(shasum -a 256 "$RG_CACHE/rg.tar.gz" | awk '{print $1}')"
  [ "$want" = "$got" ] || { echo "rg sha256 불일치: $got != $want"; exit 1; }
  tar -xzf "$RG_CACHE/rg.tar.gz" -C "$RG_CACHE" --strip-components 1 "$RG_NAME/rg" "$RG_NAME/COPYING" "$RG_NAME/UNLICENSE" "$RG_NAME/LICENSE-MIT"
fi
cp "$RG_CACHE/rg" "$OUT/bin/rg"
mkdir -p "$OUT/licenses/ripgrep" && cp "$RG_CACHE"/{COPYING,UNLICENSE,LICENSE-MIT} "$OUT/licenses/ripgrep/"

# 6) cloudflared: 공식 릴리스(Cloudflare Developer ID 서명 — sign-app.sh가 다시 서명하지 않는다) + sha256 고정
CFD_CACHE="$REPO_ROOT/build/cache/cloudflared-$CFD_VER"
if [ ! -x "$CFD_CACHE/cloudflared" ]; then
  mkdir -p "$CFD_CACHE"
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/download/$CFD_VER/cloudflared-darwin-arm64.tgz" -o "$CFD_CACHE/cloudflared.tgz"
  got="$(shasum -a 256 "$CFD_CACHE/cloudflared.tgz" | awk '{print $1}')"
  [ "$got" = "$CFD_SHA256" ] || { echo "cloudflared sha256 불일치: $got != $CFD_SHA256"; exit 1; }
  tar -xzf "$CFD_CACHE/cloudflared.tgz" -C "$CFD_CACHE" cloudflared
  curl -fsSL "https://raw.githubusercontent.com/cloudflare/cloudflared/$CFD_VER/LICENSE" -o "$CFD_CACHE/LICENSE"
fi
cp "$CFD_CACHE/cloudflared" "$OUT/bin/cloudflared"
mkdir -p "$OUT/licenses/cloudflared" && cp "$CFD_CACHE/LICENSE" "$OUT/licenses/cloudflared/"

# 7) 빌드 정보 (앱이 백엔드 버전·프로토콜을 확인한다)
"$PY" -I -B - "$OUT" "$RG_VER" "$CFD_VER" <<'EOF'
import json, sys, platform
from importlib.metadata import version
from askproject import __protocol__, __version__
from claude_agent_sdk._cli_version import __cli_version__
json.dump({"version": __version__, "protocol": __protocol__, "python": platform.python_version(),
           "claude_cli": __cli_version__, "rg": sys.argv[2], "cloudflared": sys.argv[3], "claude_agent_sdk": version("claude-agent-sdk")},
          open(f"{sys.argv[1]}/BUILD_INFO.json", "w"), indent=2)
EOF

# 8) 확인: git·셸 PATH 없이 뜨는지
env -i HOME="$HOME" "$PY" -I -B -m askproject --version
echo "백엔드 번들: $OUT ($(du -sh "$OUT" | cut -f1))"
