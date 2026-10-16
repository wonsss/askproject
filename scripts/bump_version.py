#!/usr/bin/env python3
"""버전을 적은 곳을 한 번에 맞춘다: pyproject.toml · src/askproject/__init__.py · uv.lock · 개발 앱 Info.plist.

  python3 scripts/bump_version.py patch|minor|major   # 0.1.0 → 0.1.1 / 0.2.0 / 1.0.0
  python3 scripts/bump_version.py 1.2.3              # 정확한 버전
  python3 scripts/bump_version.py --check            # 네 곳이 같은지만 확인 (현재 버전 출력)
표준 라이브러리만 쓴다(릴리스 CI·개발 맥 어디서나).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# (파일, 버전을 찾는 정규식 — 그룹 1이 버전)
SPOTS = [
    ("pyproject.toml", re.compile(r'^version = "([^"]+)"', re.M)),
    ("src/askproject/__init__.py", re.compile(r'^__version__ = "([^"]+)"', re.M)),
    ("uv.lock", re.compile(r'^name = "askproject"\nversion = "([^"]+)"', re.M)),
    ("macos/AskProjectBar/Info.plist", re.compile(r"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>")),
]


def read_all() -> dict[str, str]:
    out = {}
    for name, rx in SPOTS:
        m = rx.search((ROOT / name).read_text(encoding="utf-8"))
        if not m:
            sys.exit(f"{name}에서 버전을 찾지 못했어요")
        out[name] = m.group(1)
    return out


def next_version(current: str, how: str) -> str:
    if SEMVER.match(how):
        return how
    m = SEMVER.match(current)
    if not m:
        sys.exit(f"현재 버전 {current}이 X.Y.Z 형식이 아니에요")
    major, minor, patch = map(int, m.groups())
    if how == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if how == "minor":
        return f"{major}.{minor + 1}.0"
    if how == "major":
        return f"{major + 1}.0.0"
    sys.exit(f"patch·minor·major 또는 X.Y.Z를 주세요: {how}")


def key(v: str) -> tuple[int, ...]:
    return tuple(map(int, v.split(".")))


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        sys.exit(__doc__)
    found = read_all()
    versions = set(found.values())
    if argv[0] == "--check":
        if len(versions) != 1:
            sys.exit("버전이 서로 달라요: " + ", ".join(f"{k}={v}" for k, v in found.items()))
        print(versions.pop())
        return
    current = found["pyproject.toml"]
    new = next_version(current, argv[0])
    if key(new) <= key(current):
        sys.exit(f"새 버전 {new}은 현재 {current}보다 커야 해요")
    for name, rx in SPOTS:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        m = rx.search(text)
        path.write_text(text[: m.start(1)] + new + text[m.end(1):], encoding="utf-8")
    print(new)


if __name__ == "__main__":
    main(sys.argv[1:])
