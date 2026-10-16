"""환경 설정. 비밀값은 코드·레포에 두지 않는다 — 개발 모드는 .env(또는 환경변수), 앱 모드는 Keychain.

경로 기본값은 실행 모드에 따라 다르다(askproject.paths).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from askproject import paths

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


def update_env_file(path: Path, key: str, value: str | None) -> None:
    """`.env`의 KEY=value 한 줄을 바꾼다(None이면 지운다). 없으면 끝에 붙인다. 주석·다른 키는 그대로 둔다."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}" if value is not None else ""
            replaced = True
            break
    if not replaced and value is not None:
        lines.append(f"{key}={value}")
    lines = [x for i, x in enumerate(lines) if x or (i and lines[i - 1])]  # 지운 줄이 빈 줄로 쌓이지 않게
    paths.atomic_write(path, "\n".join(lines).strip("\n") + "\n")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ASKPROJECT_",
        extra="ignore",
    )

    # 경로 (기본값은 실행 모드별, askproject.paths)
    data_dir: Path = Field(default_factory=paths.default_data_dir, description="DB·상태 저장 위치")
    # 비우면 앱 모드는 ~/Library/Caches·Logs, 개발·서버 모드는 data_dir 아래(데이터 폴더 하나만 마운트하면 되게)
    cache_dir: Path | None = Field(default=None, description="레포 사본 등 다시 만들 수 있는 것")
    log_dir: Path | None = None
    # DB 운영 (docs/operations.md). 감사 로그는 영구 보존, 백업 파일만 keep_days 지나면 로컬에서 정리
    backup_dir: Path = Field(default_factory=paths.default_backup_dir, description="로컬 백업 위치")
    backup_keep_days: int = 14
    backup_upload_cmd: str | None = Field(default=None, description="서버 밖 복사 명령. {file}이 백업 경로로 바뀜")
    config_dir: Path = Field(default_factory=paths.default_config_dir)

    # Claude
    # api: .env의 ANTHROPIC_API_KEY로 직접 호출. subscription: 로컬 `claude` CLI 구독 로그인을 하위
    # 프로세스로 띄워 사용(SlackBotManager 방식). 구독 모드는 Anthropic 정책상 타인에게 서비스하는 용도로
    # 쓰면 안 되므로 로컬 PoC 전용.
    llm_provider: Literal["api", "subscription"] = "api"
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    # 셸의 ANTHROPIC_BASE_URL은 쓰지 않는다. .env의 ANTHROPIC_BASE_URL 또는 ASKPROJECT_ANTHROPIC_BASE_URL만 반영한다.
    anthropic_base_url: str = Field(default=DEFAULT_ANTHROPIC_BASE_URL, validation_alias="ASKPROJECT_ANTHROPIC_BASE_URL")
    model_large: str = "claude-sonnet-5-5"
    model_small: str = "claude-haiku-5-5"
    # 에이전트 effort (low|medium|high|xhigh|max). 비우면 모델 기본값: sonnet-5-5 high, haiku-5-5 medium
    agent_effort_large: str | None = None
    agent_effort_small: str | None = None
    agent_max_turns: int = 12
    agent_max_budget_usd: float = 0.5  # api 모드만 적용 (구독은 토큰 과금이 없어 무시)

    # 맥북 호스트 모드 절전 정책 (host.py의 caffeinate 인자)
    # default: 절전 허용(봇이 맥과 같이 잠듦) · lid-open: 덮개 열린 동안 깨어 있음
    # lid-closed: 전원 연결 시 덮개를 닫아도 깨어 있음(caffeinate -s, 배터리로는 불가)
    power_mode: Literal["default", "lid-open", "lid-closed"] = "lid-open"

    # GitHub — GitHub App 우선, 없으면 fine-grained PAT(contents 읽기). 레포는 API(tarball·trees)로 받는다(git 불필요)
    github_api_url: str = "https://api.github.com"
    github_app_id: str | None = None
    github_app_private_key_path: Path | None = None
    github_app_installation_id: str | None = None
    github_token: SecretStr | None = None

    # Slack (Socket Mode)
    slack_bot_token: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    escalation_channel: str | None = Field(default=None, description="'개발자에게 확인 요청'을 올릴 채널 ID")
    # 봇이 답할 채널 ID(쉼표 구분). 비어 있으면 봇이 초대된 모든 채널 — PAT 권한이 그 채널 전원에게 열리므로 경고한다.
    # DM을 허용하려면 'dm'을 넣는다.
    allowed_channels: str = ""
    # 호스트 하트비트 채널 ID. 같은 슬랙 앱으로 봇이 두 대 켜지면 이벤트가 나뉘므로 감지한다. 비면 escalation_channel
    host_channel: str | None = None

    # 웹 콘솔 팀 공개(Cloudflare Tunnel + Access). 셋 다 있어야 공개 호스트 요청을 받는다(Access JWT 검증)
    console_public_host: str | None = None  # 예: askproject.example.com
    cf_access_team_domain: str | None = None  # 예: myteam.cloudflareaccess.com
    cf_access_aud: str | None = None  # Access 애플리케이션의 Application Audience (AUD) 태그

    # 단순 호출 비용 추정용 단가 (USD / 100만 토큰). 현재 가격표에 맞게 설정

    # 노션 (봇 전용 내부 통합, 읽기 전용)
    notion_token: SecretStr | None = None
    notion_root_page_id: str | None = None

    # 슬랙 소스 채널 (공개 채널 ID, 쉼표 구분) · 기간
    slack_source_channels: str = ""
    slack_source_days: int = 90

    # 도구 상한
    search_max_results: int = 20
    read_max_lines: int = 200

    _env_path: Path = PrivateAttr(default=Path(".env"))

    def __init__(self, **values):
        # 설정 파일 위치도 실행 모드별이다(개발 .env, 앱 config/settings.env)
        values.setdefault("_env_file", paths.default_env_file())
        super().__init__(**values)
        self._env_path = Path(values["_env_file"]) if values["_env_file"] else Path(".env")
        self._after_load()

    def _after_load(self) -> None:
        if self.cache_dir is None:
            self.cache_dir = paths.default_cache_dir() if paths.app_mode() else self.data_dir
        if self.log_dir is None:
            self.log_dir = paths.default_log_dir() if paths.app_mode() else self.data_dir / "logs"
        # ANTHROPIC_* 는 이름이 다른 도구(Claude Code 등)와 겹친다. 셸이 물려준 값이 .env의 봇 전용 키·주소를 덮어쓰면
        # 401(invalid x-api-key)이나 엉뚱한 엔드포인트 호출이 나므로, 이 둘은 .env를 우선한다.
        # ASKPROJECT_* 설정은 지금처럼 환경변수가 우선이다(실험용 덮어쓰기).
        values = dotenv_values(self._env_path) if self._env_path.is_file() else {}
        if values.get("ANTHROPIC_API_KEY"):
            self.anthropic_api_key = SecretStr(values["ANTHROPIC_API_KEY"])
        if values.get("ANTHROPIC_BASE_URL"):
            self.anthropic_base_url = values["ANTHROPIC_BASE_URL"]
        if paths.app_mode():
            # 앱 모드: 비밀값은 Keychain이 기준이다(셸에서 런처를 돌려도 셸의 키가 끼어들지 않게 덮어쓴다)
            from askproject import keychain

            for field, value in keychain.load_all().items():
                setattr(self, field, SecretStr(value))
            # 구독 로그인은 본인 PoC 전용이다. 슬랙 봇은 타인에게 답하므로 제품에서는 쓰지 않는다
            if self.llm_provider == "subscription":
                raise ValueError("앱에서는 구독(subscription) 모드를 쓸 수 없어요. Claude API 키를 쓰세요.")

    @property
    def env_path(self) -> Path:
        return self._env_path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "askproject.sqlite3"

    @property
    def repos_dir(self) -> Path:
        return self.cache_dir / "repos"

    @property
    def claude_config_dir(self) -> Path:
        """에이전트 하위 claude CLI 전용 설정 폴더. 사용자의 ~/.claude(설정·훅·MCP)와 섞이지 않게 한다."""
        return self.data_dir / "claude"

    def allowed_channel_ids(self) -> set[str]:
        return {c.strip() for c in self.allowed_channels.split(",") if c.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
