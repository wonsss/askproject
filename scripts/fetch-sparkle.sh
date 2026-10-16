#!/bin/bash
# Sparkle 프레임워크를 build/cache에 받는다(고정 버전 + sha256 확인).
set -euo pipefail
OUT="${1:?출력 폴더}"
VER="2.10.0"
SPARKLE_SHA256="${SPARKLE_SHA256:-c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c}"
[ -d "$OUT/Sparkle.framework" ] && [ "$(cat "$OUT/VERSION" 2>/dev/null)" = "$VER" ] && exit 0
rm -rf "$OUT" && mkdir -p "$OUT"
curl -fsSL "https://github.com/sparkle-project/Sparkle/releases/download/$VER/Sparkle-$VER.tar.xz" -o "$OUT/sparkle.tar.xz"
got="$(shasum -a 256 "$OUT/sparkle.tar.xz" | awk '{print $1}')"
if [ -n "${SPARKLE_SHA256:-}" ] && [ "$got" != "$SPARKLE_SHA256" ]; then echo "Sparkle sha256 불일치: $got"; exit 1; fi
tar -xf "$OUT/sparkle.tar.xz" -C "$OUT"
echo "$VER" > "$OUT/VERSION"
echo "Sparkle $VER sha256 $got"
