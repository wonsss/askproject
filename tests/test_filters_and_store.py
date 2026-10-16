import sqlite3

import pytest

from askproject.indexing.filters import PathFilter, matches
from askproject.store.db import QueryResult


def test_secret_files_always_blocked():
    f = PathFilter(exclude=())
    assert not f.allowed(".env")
    assert not f.allowed("apps/api/.env")
    assert not f.allowed("apps/web/.env.local")
    assert not f.allowed("secrets/github-app.pem")
    assert f.allowed("apps/api/.env.example")


def test_include_exceptions_cannot_unblock_secrets():
    """레포 설정의 예외 글롭이 넓어도 비밀값·키 파일은 풀리지 않는다(.env.example만 예외)."""
    f = PathFilter((), ("config/**",))
    for p in ("config/.env", "config/prod.pem", "config/.npmrc", "home/.ssh/id_ed25519", ".aws/credentials"):
        assert not f.allowed(p), p
    assert f.allowed("config/app.yaml") and f.allowed("config/.env.example")


def test_path_traversal_blocked():
    f = PathFilter(exclude=())
    assert not f.allowed("../etc/passwd")
    assert not f.allowed("apps/../../x.ts")
    assert not f.allowed("/etc/passwd")


def test_glob_semantics():
    assert matches("a/b/c.png", ["**/*.{png,jpg}"])
    assert matches("c.png", ["**/*.png"])  # **/ 는 루트도 포함
    assert matches("archive/x/y.ts", ["archive/**"])
    assert not matches("src/archive.ts", ["archive/**"])
    assert matches("apps/worker/scripts/a-results.json", ["apps/worker/scripts/*-results.json"])


def test_audit_log_is_append_only(store):
    qid = store.log_query("질문", "cli")
    store.log_result(qid, QueryResult(status="ok", answer="답"))
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE queries SET question='변조' WHERE id=?", (qid,))
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM query_results WHERE query_id=?", (qid,))


def test_env_file_api_key_wins_over_shell(tmp_path, monkeypatch):
    from askproject.settings import Settings

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-dotenv\nASKPROJECT_AGENT_MAX_TURNS=5\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-shell-key")
    monkeypatch.setenv("ASKPROJECT_AGENT_MAX_TURNS", "9")
    s = Settings()
    assert s.anthropic_api_key.get_secret_value() == "from-dotenv"
    assert s.agent_max_turns == 9  # ASKPROJECT_* 는 환경변수가 우선(실험용 덮어쓰기 유지)


def test_shell_base_url_is_ignored_unless_in_env_file(tmp_path, monkeypatch):
    from askproject.settings import DEFAULT_ANTHROPIC_BASE_URL, Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=k\n", encoding="utf-8")
    assert Settings().anthropic_base_url == DEFAULT_ANTHROPIC_BASE_URL
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=k\nANTHROPIC_BASE_URL=https://gw.example\n", encoding="utf-8")
    assert Settings().anthropic_base_url == "https://gw.example"


def test_agent_env_overrides_inherited_claude_auth():
    from askproject.agent.claude_runner import ClaudeAgentRunner

    env = ClaudeAgentRunner("m", "bot-key", "https://api.anthropic.com")._env()
    from askproject.agent.claude_runner import QUIET_ENV

    assert env == {"ANTHROPIC_API_KEY": "bot-key", "ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                   **QUIET_ENV}


def test_claude_subprocess_sends_no_telemetry():
    """번들 claude는 기본으로 사용 지표를 Anthropic·제3자 로깅에 보낸다 — 봇은 API 호출 외 통신을 모두 끈다"""
    from askproject.agent.claude_runner import ClaudeAgentRunner
    from askproject.llm.claude_cli import cli_env

    for env in (ClaudeAgentRunner("m", "k")._env(), ClaudeAgentRunner("m", None, subscription=True)._env(), cli_env()):
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1" and env["DISABLE_TELEMETRY"] == "1"
        assert env["DISABLE_ERROR_REPORTING"] == "1" and env["DISABLE_FEEDBACK_COMMAND"] == "1"


def test_agent_isolated_from_user_claude_config(tmp_path):
    """API 모드: 전용 CLAUDE_CONFIG_DIR·자동 업데이트 끔 — 사용자의 ~/.claude 설정·훅·MCP가 섞이지 않게"""
    from askproject.agent.claude_runner import ClaudeAgentRunner

    cfg = tmp_path / "claude"
    env = ClaudeAgentRunner("m", "k", config_dir=str(cfg))._env()
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg) and cfg.is_dir() and env["DISABLE_AUTOUPDATER"] == "1"
    sub = ClaudeAgentRunner("m", None, subscription=True, config_dir=str(cfg))._env()
    assert "CLAUDE_CONFIG_DIR" not in sub  # 구독(개발 PoC)은 사용자 로그인을 써야 한다


def test_preflight_reports_rejected_key(monkeypatch):
    import anthropic
    import httpx

    from askproject.doctor import claude_preflight
    from askproject.settings import Settings

    def reject(self, **kw):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)

    monkeypatch.setattr(anthropic.resources.Messages, "create", reject)
    # .env에 ASKPROJECT_LLM_PROVIDER가 있을 수 있어 이 테스트는 api 모드를 명시한다
    msg = claude_preflight(Settings(ANTHROPIC_API_KEY="bad", llm_provider="api"))
    assert msg and "401" in msg and "bad" not in msg


def test_escalation_channel_falls_back_to_host_channel(tmp_path, monkeypatch):
    from askproject.settings import Settings

    monkeypatch.chdir(tmp_path)
    assert Settings(host_channel="C_OPS").escalation_channel_id() == "C_OPS"
    assert Settings(host_channel="C_OPS", escalation_channel="C_DEV").escalation_channel_id() == "C_DEV"
    assert Settings(host_channel="", escalation_channel="").escalation_channel_id() is None
