"""실행 모드별 경로 한곳. 앱(메뉴 막대)과 백엔드가 같은 값을 쓰도록 `askproject paths --json`으로 내보낸다.

- 개발 모드(기본): 레포 기준 상대 경로 — .env · config/ · data/ · backups/
- 앱 모드(ASKPROJECT_APP=1, 번들 런처가 설정): ~/Library 아래로 분리한다
  상태 Application Support/AskProject · 재생성 가능(레포·인덱스 사본) Caches/AskProject · 로그 Logs/AskProject
  비밀값은 파일이 아니라 Keychain(askproject.keychain)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

APP_NAME = "AskProject"


def app_mode() -> bool:
    return os.environ.get("ASKPROJECT_APP") == "1"


def _library(sub: str) -> Path:
    return Path.home() / "Library" / sub / APP_NAME


def app_support() -> Path:
    return _library("Application Support")


def default_data_dir() -> Path:
    return app_support() / "data" if app_mode() else Path("data")


def default_config_dir() -> Path:
    return app_support() / "config" if app_mode() else Path("config")


def default_cache_dir() -> Path:
    """앱 모드 전용 기본값. 개발·서버 모드는 Settings가 data_dir를 쓴다."""
    return _library("Caches")


def default_backup_dir() -> Path:
    return app_support() / "backups" if app_mode() else Path("backups")


def default_log_dir() -> Path:
    return _library("Logs")


def default_env_file() -> Path:
    """비밀값이 아닌 설정 파일. 앱 모드에서는 `askproject config set`만 쓴다."""
    return default_config_dir() / "settings.env" if app_mode() else Path(".env")


def eval_results_dir() -> Path:
    return default_data_dir() / "eval-results" if app_mode() else Path("eval/results")


def bundle_dir() -> Path | None:
    """앱 번들의 Contents/Resources/backend (런처가 알려 준다). 개발 모드면 None."""
    v = os.environ.get("ASKPROJECT_BUNDLE_DIR")
    return Path(v) if v else None


def rg_path() -> str | None:
    """rg 실행 파일: ASKPROJECT_RG_PATH → 번들 내장 → PATH 순."""
    explicit = os.environ.get("ASKPROJECT_RG_PATH")
    if explicit:
        return explicit
    b = bundle_dir()
    if b and (b / "bin" / "rg").is_file():
        return str(b / "bin" / "rg")
    return shutil.which("rg")


def claude_cli_path() -> str | None:
    """claude-agent-sdk 휠에 들어 있는 claude 바이너리. SDK의 자동 탐색 대신 명시해 사용자의 다른 claude와 섞이지 않게 한다."""
    import claude_agent_sdk

    p = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(p) if p.is_file() else None


def ensure_app_dirs(settings) -> None:
    """앱 모드 첫 실행: 폴더를 만들고(본인만 읽기), 설정 파일이 없으면 기본 템플릿을 둔다."""
    for d in (settings.config_dir, settings.data_dir, settings.cache_dir, settings.backup_dir, settings.log_dir):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    defaults = Path(__file__).parent / "defaults"
    for name in ("repos.yaml", "domains.yaml", "glossary.yaml"):
        target = settings.config_dir / name
        if not target.exists():
            shutil.copyfile(defaults / name, target)


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """같은 폴더의 임시 파일에 쓰고 rename한다. 도중에 죽어도 반쯤 쓴 설정 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def describe(settings) -> dict:
    """`askproject paths --json` — 앱이 로그 폴더 열기 등에 쓰는 경로 목록."""
    from askproject import __protocol__, __version__

    return {
        "version": __version__,
        "protocol": __protocol__,
        "mode": "app" if app_mode() else "dev",
        "config_dir": str(settings.config_dir.resolve()),
        "env_file": str(default_env_file().resolve()),
        "data_dir": str(settings.data_dir.resolve()),
        "cache_dir": str(settings.cache_dir.resolve()),
        "repos_dir": str(settings.repos_dir.resolve()),
        "backup_dir": str(settings.backup_dir.resolve()),
        "log_dir": str(settings.log_dir.resolve()),
        "db_path": str(settings.db_path.resolve()),
        "claude_config_dir": str(settings.claude_config_dir.resolve()),
        "bundle_dir": str(bundle_dir()) if bundle_dir() else None,
        "rg": rg_path(),
        "claude_cli": claude_cli_path(),
        "secrets": "keychain" if app_mode() else "env_file",
    }
