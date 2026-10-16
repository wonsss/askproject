"""앱 모드의 비밀값 저장소(macOS 로그인 Keychain). /usr/bin/security를 쓴다.

- 쓰기는 `security -i`의 표준 입력으로 넘긴다. 명령 인자로 넘기면 ps로 보이기 때문이다.
- 항목을 만든 /usr/bin/security가 접근 목록에 들어가므로, 백그라운드 에이전트가 읽을 때 대화 상자가 뜨지 않는다.
  같은 사용자로 도는 다른 프로세스도 security로 읽을 수 있다는 뜻이다 — 권한 0600 파일과 같은 수준이고,
  디스크·백업(Time Machine)에 평문으로 남지 않는다는 점이 .env보다 낫다. (docs/packaging.md)
"""

from __future__ import annotations

import re
import subprocess

SERVICE = "com.askproject"
SECURITY = "/usr/bin/security"
NOT_FOUND = 44
# Settings 필드 → Keychain 계정 이름(= 개발 모드의 .env 키)
FIELDS: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "github_token": "ASKPROJECT_GITHUB_TOKEN",
    "slack_bot_token": "ASKPROJECT_SLACK_BOT_TOKEN",
    "slack_app_token": "ASKPROJECT_SLACK_APP_TOKEN",
    "notion_token": "ASKPROJECT_NOTION_TOKEN",
    "cloudflare_tunnel_token": "ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN",
}
_SAFE = re.compile(r"^[\x21-\x7e]+$")  # 공백·따옴표·역슬래시 없는 출력 가능 ASCII (토큰·키 형식)


class KeychainError(RuntimeError):
    pass


def get(account: str) -> str | None:
    r = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True, timeout=15)
    if r.returncode == NOT_FOUND:
        return None
    if r.returncode != 0:
        raise KeychainError(f"Keychain 읽기 실패({account}): {r.stderr.strip()[:200]}")
    return r.stdout.rstrip("\n") or None


def put(account: str, value: str) -> None:
    if account not in FIELDS.values():
        raise KeychainError(f"알 수 없는 비밀값 이름: {account}")
    if not _SAFE.match(value) or '"' in value or "\\" in value:
        raise KeychainError(f"{account}: 공백·따옴표·역슬래시 없는 토큰 형식이어야 해요")
    cmd = f'add-generic-password -U -s "{SERVICE}" -a "{account}" -l "AskProject {account}" -w "{value}"\n'
    r = subprocess.run([SECURITY, "-i"], input=cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0 or "error" in (r.stderr + r.stdout).lower():
        raise KeychainError(f"Keychain 저장 실패({account}): {r.stderr.strip()[:200]}")


def delete(account: str) -> None:
    r = subprocess.run([SECURITY, "delete-generic-password", "-s", SERVICE, "-a", account],
                       capture_output=True, text=True, timeout=15)
    if r.returncode not in (0, NOT_FOUND):
        raise KeychainError(f"Keychain 삭제 실패({account}): {r.stderr.strip()[:200]}")


def load_all() -> dict[str, str]:
    """Settings 필드 이름 → 값 (저장된 것만)."""
    out = {}
    for field, account in FIELDS.items():
        v = get(account)
        if v:
            out[field] = v
    return out
