"""앱↔백엔드 설정 계약: `askproject config get --json` / `askproject config set --json`(표준 입력 JSON).

앱(SwiftUI)은 설정 파일을 직접 쓰지 않는다. 여기서 pydantic으로 검증하고 원자적으로 쓴다.
- 일반 설정: 설정 파일(개발 .env, 앱 config/settings.env)의 ASKPROJECT_* 키
- 비밀값: 앱 모드는 Keychain, 개발 모드는 .env. 값은 돌려주지 않고 저장 여부만 알린다.
- 레포: config/repos.yaml의 repos 목록(defaults 블록은 유지, 주석은 남지 않는다)
비밀값은 명령 인자가 아니라 표준 입력으로만 받는다(ps에 보이지 않게).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from askproject import keychain, paths
from askproject.settings import Settings, update_env_file

CONFIG_VERSION = 1
CHANNEL_ID = re.compile(r"^[CGD][A-Z0-9]{6,}$")


def _channels(v: str | None) -> str | None:
    if v is None:
        return v
    ids = [c.strip() for c in v.split(",") if c.strip()]
    bad = [c for c in ids if c != "dm" and not CHANNEL_ID.match(c)]
    if bad:
        raise ValueError(f"채널 ID 형식이 아니에요(C로 시작하는 ID 또는 dm): {', '.join(bad)}")
    return ",".join(ids)


class SettingsPatch(BaseModel):
    """앱에서 바꿀 수 있는 일반 설정. None이면 그대로 두고, 빈 문자열이면 지운다(기본값으로)."""

    model_config = ConfigDict(extra="forbid")

    model_large: str | None = None
    model_small: str | None = None
    agent_effort_large: Literal["", "low", "medium", "high", "xhigh", "max"] | None = None
    agent_effort_small: Literal["", "low", "medium", "high", "xhigh", "max"] | None = None
    agent_max_budget_usd: float | None = Field(default=None, gt=0, le=20)
    power_mode: Literal["default", "lid-open", "lid-closed"] | None = None
    escalation_channel: str | None = None
    host_channel: str | None = None
    allowed_channels: str | None = None
    slack_source_channels: str | None = None
    slack_source_days: int | None = Field(default=None, ge=1, le=365)
    notion_root_page_id: str | None = None

    @field_validator("allowed_channels", "slack_source_channels")
    @classmethod
    def _channel_list(cls, v: str | None) -> str | None:
        return _channels(v)

    @field_validator("escalation_channel", "host_channel")
    @classmethod
    def _one_channel(cls, v: str | None) -> str | None:
        if v and not CHANNEL_ID.match(v.strip()):
            raise ValueError(f"채널 ID 형식이 아니에요: {v}")
        return v.strip() if v is not None else v


SECRET_PREFIX = {
    "ANTHROPIC_API_KEY": "sk-ant-",
    "ASKPROJECT_GITHUB_TOKEN": "github_pat_",
    "ASKPROJECT_SLACK_BOT_TOKEN": "xoxb-",
    "ASKPROJECT_SLACK_APP_TOKEN": "xapp-",
    "ASKPROJECT_NOTION_TOKEN": None,
}


class RepoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    url: str
    ref: str = Field(default="main", min_length=1, max_length=200)
    description: str = ""

    @field_validator("url")
    @classmethod
    def _github(cls, v: str) -> str:
        from askproject.indexing.repo_sync import parse_github_url

        owner, name = parse_github_url(v)
        return f"https://github.com/{owner}/{name}"


class ConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: SettingsPatch = Field(default_factory=SettingsPatch)
    secrets: dict[str, str] = Field(default_factory=dict)
    repos: list[RepoInput] | None = None

    @field_validator("secrets")
    @classmethod
    def _secrets(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if key not in SECRET_PREFIX:
                raise ValueError(f"알 수 없는 비밀값: {key}")
            prefix = SECRET_PREFIX[key]
            # GitHub은 classic PAT(ghp_)도 받는다 — 다만 앱은 fine-grained를 안내한다
            ok_prefixes = (prefix, "ghp_") if key == "ASKPROJECT_GITHUB_TOKEN" else (prefix,)
            if value and prefix and not value.startswith(ok_prefixes):
                raise ValueError(f"{key}는 '{prefix}'로 시작해야 해요")
        return v


def _env_key(field: str) -> str:
    return f"ASKPROJECT_{field.upper()}"


def get(settings: Settings) -> dict:
    """현재 설정(비밀값은 저장 여부만)."""
    raw = yaml.safe_load((settings.config_dir / "repos.yaml").read_text(encoding="utf-8")) \
        if (settings.config_dir / "repos.yaml").is_file() else {}
    secrets = {}
    for field, account in keychain.FIELDS.items():
        secrets[account] = bool(getattr(settings, field))
    return {
        "config_version": CONFIG_VERSION,
        "mode": "app" if paths.app_mode() else "dev",
        "settings": {f: getattr(settings, f) for f in SettingsPatch.model_fields},
        "secrets": secrets,
        "repos": [{k: r.get(k, "") for k in ("name", "url", "ref", "description")} for r in (raw or {}).get("repos") or []],
    }


def apply(settings: Settings, patch: ConfigPatch) -> list[str]:
    """검증된 변경을 쓴다. 바뀐 항목 이름 목록을 돌려준다."""
    changed: list[str] = []
    env = settings.env_path
    for field, value in patch.settings.model_dump(exclude_none=True).items():
        update_env_file(env, _env_key(field), None if value == "" else str(value))
        changed.append(field)
    for account, value in patch.secrets.items():
        if paths.app_mode():
            keychain.put(account, value) if value else keychain.delete(account)
        else:
            update_env_file(env, account, value or None)
        changed.append(account)
    if patch.repos is not None:
        write_repos(settings.config_dir, [r.model_dump() for r in patch.repos])
        changed.append("repos")
    return changed


def write_repos(config_dir: Path, repos: list[dict]) -> None:
    path = config_dir / "repos.yaml"
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
    if "defaults" not in raw:
        defaults = yaml.safe_load((Path(__file__).parent / "defaults" / "repos.yaml").read_text(encoding="utf-8"))
        raw["defaults"] = defaults.get("defaults", {})
    by_name = {r.get("name"): r for r in raw.get("repos") or []}
    out = []
    for r in repos:
        merged = {**by_name.get(r["name"], {}), **{k: v for k, v in r.items() if v != "" or k == "description"}}
        out.append(merged)  # 폼에 없는 키(exclude·structure 등)는 기존 값을 유지한다
    raw["repos"] = out
    header = "# askproject config set이 쓴 파일입니다. 앱 설정 화면에서 바꾸세요.\n"
    paths.atomic_write(path, header + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), mode=0o644)


def set_from_json(settings: Settings, text: str) -> dict:
    patch = ConfigPatch.model_validate(json.loads(text))
    changed = apply(settings, patch)
    return {"ok": True, "changed": changed}
