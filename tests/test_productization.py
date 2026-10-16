"""데스크톱 제품화: 실행 모드별 경로·Keychain·설정 계약·GitHub API 동기화·로그 회전."""

import io
import json
import tarfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import yaml

from askproject import config_store, keychain, logs, paths
from askproject.config import RepoConfig
from askproject.indexing import repo_sync
from askproject.indexing.repo_sync import RepoSync, git_blob_sha, parse_expiry, parse_github_url, token_expiry_status
from askproject.settings import Settings


@pytest.fixture
def app_home(tmp_path, monkeypatch):
    """앱 모드 + 가짜 홈 + 메모리 Keychain."""
    monkeypatch.setenv("ASKPROJECT_APP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "ASKPROJECT_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    store: dict[str, str] = {}
    monkeypatch.setattr(keychain, "get", lambda a: store.get(a))
    monkeypatch.setattr(keychain, "put", lambda a, v: store.__setitem__(a, v))
    monkeypatch.setattr(keychain, "delete", lambda a: store.pop(a, None))
    return tmp_path, store


def test_dev_mode_paths_unchanged(monkeypatch):
    monkeypatch.delenv("ASKPROJECT_APP", raising=False)
    s = Settings(_env_file=None)
    assert (s.data_dir, s.config_dir, s.repos_dir) == (paths.Path("data"), paths.Path("config"), paths.Path("data/repos"))
    assert paths.default_env_file() == paths.Path(".env")
    moved = Settings(_env_file=None, data_dir=paths.Path("/data"))  # 서버(docker): 데이터 폴더 하나만 마운트
    assert moved.repos_dir == paths.Path("/data/repos") and moved.log_dir == paths.Path("/data/logs")


def test_app_mode_separates_state_cache_logs_and_reads_keychain(app_home, monkeypatch):
    home, store = app_home
    store["ANTHROPIC_API_KEY"] = "sk-ant-from-keychain"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")  # 셸 값이 끼어들지 않아야 한다
    s = Settings()
    lib = home / "Library"
    assert s.data_dir == lib / "Application Support/AskProject/data"
    assert s.repos_dir == lib / "Caches/AskProject/repos"  # 다시 받을 수 있는 것은 Caches
    assert s.log_dir == lib / "Logs/AskProject"
    assert s.env_path == lib / "Application Support/AskProject/config/settings.env"
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-from-keychain"
    paths.ensure_app_dirs(s)
    assert (s.config_dir / "domains.yaml").is_file() and oct(s.data_dir.stat().st_mode)[-3:] == "700"
    assert "repos:" in (s.config_dir / "repos.yaml").read_text()
    d = paths.describe(s)
    assert d["mode"] == "app" and d["secrets"] == "keychain" and " " in d["data_dir"]  # 'Application Support' 공백


def test_app_mode_rejects_subscription(app_home, monkeypatch):
    monkeypatch.setenv("ASKPROJECT_LLM_PROVIDER", "subscription")
    with pytest.raises(ValueError, match="구독"):
        Settings()


def test_config_set_writes_settings_secrets_repos_atomically(app_home):
    _, store = app_home
    s = Settings()
    paths.ensure_app_dirs(s)
    patch = {"settings": {"allowed_channels": "C0123ABCD, dm", "power_mode": "lid-closed"},
             "secrets": {"ASKPROJECT_SLACK_BOT_TOKEN": "xoxb-1-2-abc", "ASKPROJECT_GITHUB_TOKEN": "github_pat_X"},
             "repos": [{"name": "svc", "url": "https://github.com/acme/svc.git", "ref": "main"}]}
    out = config_store.set_from_json(s, json.dumps(patch))
    assert out["ok"] and "repos" in out["changed"]
    assert store["ASKPROJECT_SLACK_BOT_TOKEN"] == "xoxb-1-2-abc"  # 비밀값은 Keychain으로
    env_text = s.env_path.read_text()
    assert "xoxb" not in env_text and "ASKPROJECT_ALLOWED_CHANNELS=C0123ABCD,dm" in env_text
    assert oct(s.env_path.stat().st_mode)[-3:] == "600"
    raw = yaml.safe_load((s.config_dir / "repos.yaml").read_text())
    assert raw["repos"][0]["url"] == "https://github.com/acme/svc" and raw["defaults"]["exclude"]
    got = config_store.get(Settings())
    assert got["secrets"]["ASKPROJECT_SLACK_BOT_TOKEN"] is True and got["secrets"]["ANTHROPIC_API_KEY"] is False
    assert got["settings"]["power_mode"] == "lid-closed" and got["repos"][0]["name"] == "svc"
    assert "xoxb" not in json.dumps(got)  # 값은 돌려주지 않는다


@pytest.mark.parametrize("patch, msg", [
    ({"secrets": {"ASKPROJECT_SLACK_BOT_TOKEN": "xapp-wrong"}}, "xoxb-"),
    ({"settings": {"allowed_channels": "general"}}, "채널 ID"),
    ({"repos": [{"name": "x", "url": "https://gitlab.com/a/b"}]}, "GitHub"),
    ({"settings": {"llm_provider": "subscription"}}, "Extra"),  # 구독 전환은 계약에 없다
])
def test_config_set_rejects_invalid(app_home, patch, msg):
    with pytest.raises(Exception, match=msg):
        config_store.set_from_json(Settings(), json.dumps(patch))


def test_config_set_backup_copy_dir(app_home):
    home, _ = app_home
    s = Settings()
    paths.ensure_app_dirs(s)
    sync = home / "Library/Mobile Documents/com~apple~CloudDocs/AskProject"
    sync.mkdir(parents=True)
    config_store.set_from_json(s, json.dumps({"settings": {"backup_copy_dir": str(sync)}}))
    assert Settings().backup_copy_dir == sync.resolve()
    for bad, msg in [(home / "nope", "폴더가 없어요"), (s.backup_dir, "백업 폴더"), (s.backup_dir / "pre-migrate", "백업 폴더")]:
        bad.mkdir(parents=True, exist_ok=True) if msg != "폴더가 없어요" else None
        with pytest.raises(Exception, match=msg):
            config_store.set_from_json(s, json.dumps({"settings": {"backup_copy_dir": str(bad)}}))
    config_store.set_from_json(s, json.dumps({"settings": {"backup_copy_dir": ""}}))  # 지우기
    assert Settings().backup_copy_dir is None


def test_keychain_rejects_values_that_would_break_quoting():
    with pytest.raises(keychain.KeychainError):
        keychain.put("ANTHROPIC_API_KEY", 'sk-ant-"; rm -rf ~')


def test_github_helpers():
    assert parse_github_url("https://github.com/acme/svc.git") == ("acme", "svc")
    with pytest.raises(ValueError):
        parse_github_url("git@github.com:acme/svc.git")
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object와 같은 값
    assert parse_expiry("2026-11-15 10:00:00 UTC") == datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    assert parse_expiry("2026-11-15 10:00:00 +0900").hour == 1


def _tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in {**files, "../escape.txt": b"x"}.items():
            info = tarfile.TarInfo(f"acme-svc-abc/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("acme-svc-abc/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    return buf.getvalue()


class FakeGitHub:
    def __init__(self):
        self.sha = "sha1"
        self.files = {"a.ts": b"export const A = 1\n", "docs/b.md": b"# B\n"}
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["authorization"] == "Bearer github_pat_T"
        exp = {"github-authentication-token-expiration": "2026-11-20 00:00:00 UTC"}
        if path.endswith("/commits/main"):
            return httpx.Response(200, text=self.sha, headers=exp)
        if "/git/trees/" in path:
            tree = [{"path": p, "type": "blob", "mode": "100644", "sha": git_blob_sha(d)} for p, d in self.files.items()]
            return httpx.Response(200, json={"tree": tree, "truncated": False}, headers=exp)
        if "/tarball/" in path:
            self.downloads += 1
            return httpx.Response(200, content=_tarball(self.files))
        return httpx.Response(404)


def test_repo_sync_via_api_without_git(tmp_path, monkeypatch):
    monkeypatch.delenv("ASKPROJECT_APP", raising=False)
    s = Settings(_env_file=None, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", github_token="github_pat_T")
    gh = FakeGitHub()
    sync = RepoSync(s, client=httpx.Client(transport=httpx.MockTransport(gh)))
    repo = RepoConfig(name="svc", url="https://github.com/acme/svc")

    r = sync.sync(repo)
    root = sync.path_for(repo)
    assert r.full and r.commit_sha == "sha1" and (root / "a.ts").read_bytes() == gh.files["a.ts"]
    assert not (root / "link").exists() and not (tmp_path / "cache" / "escape.txt").exists()  # 링크·탈출 경로 무시
    assert dict(sync.tree(repo))["docs/b.md"] == git_blob_sha(b"# B\n") and sync.head(repo) == "sha1"

    again = sync.sync(repo)  # 기준 커밋이 같으면 받지 않는다
    assert again.changed_files == [] and gh.downloads == 1

    gh.sha, gh.files = "sha2", {"a.ts": b"export const A = 2\n", "c.py": b"x = 1\n"}
    r2 = sync.sync(repo)
    assert r2.previous_sha == "sha1" and r2.changed_files == ["a.ts", "c.py", "docs/b.md"]  # 수정·추가·삭제
    assert not (root / "docs").exists() and (root / "c.py").is_file()

    exp = token_expiry_status(s.data_dir, warn_days=7)
    assert exp["expires_at"].startswith("2026-11-20")
    monkeypatch.setattr(repo_sync, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: datetime(2026, 11, 15, tzinfo=timezone.utc)),
        "fromisoformat": staticmethod(datetime.fromisoformat)}))
    soon = token_expiry_status(s.data_dir, warn_days=7)
    assert soon["warning"] and 4 < soon["days_left"] < 6


def test_repo_sync_explains_missing_access(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, cache_dir=tmp_path, github_token="github_pat_T")
    sync = RepoSync(s, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(RuntimeError, match="레포 선택"):
        sync.sync(RepoConfig(name="svc", url="https://github.com/acme/svc"))


def test_log_rotation_keeps_open_descriptor_valid(tmp_path):
    path = tmp_path / "bot.log"
    with path.open("ab") as f:
        f.write(b"x" * 100)
        f.flush()
        assert logs.rotate_if_needed(path, max_bytes=50)
        f.write(b"after\n")  # O_APPEND라 비운 뒤에도 같은 파일 끝에 이어 쓴다
    assert path.read_bytes() == b"after\n" and (tmp_path / "bot.log.1").stat().st_size == 100
    assert not logs.rotate_if_needed(path, max_bytes=50)


def test_dev_token_expiry_absent(tmp_path):
    assert token_expiry_status(tmp_path) is None
    (tmp_path / "github_token.json").write_text(json.dumps({"expires_at": None}))
    assert token_expiry_status(tmp_path) == {"expires_at": None, "days_left": None, "warning": False}
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    (tmp_path / "github_token.json").write_text(json.dumps({"expires_at": future}))
    assert token_expiry_status(tmp_path)["warning"] is False


# ---- M2: 거버넌스·보안 ----

def test_channel_allowlist():
    from askproject.slack.app import channel_allowed

    assert channel_allowed(set(), "C1") and channel_allowed(None, "D1")  # 비어 있으면 전부(시작 시 경고)
    allowed = {"C0123ABCD"}
    assert channel_allowed(allowed, "C0123ABCD") and not channel_allowed(allowed, "C999")
    assert not channel_allowed(allowed, "D0123") and channel_allowed(allowed | {"dm"}, "D0123")


def test_console_requires_host_and_session_token(tmp_path):
    from starlette.testclient import TestClient

    from askproject.console.data import ConsoleData
    from askproject.console.server import COOKIE, create_app
    from askproject.store.db import Store

    Store(tmp_path / "db.sqlite3")
    data = ConsoleData(tmp_path / "db.sqlite3", "demo", "https://github.com/x/demo", tmp_path)
    app = create_app(data, allowed_hosts={"127.0.0.1:8787"}, token="t" * 40)
    c = TestClient(app, base_url="http://127.0.0.1:8787", follow_redirects=False)
    assert c.get("/").status_code == 401  # 토큰 없음
    assert c.get("/?key=wrong").status_code == 401
    r = c.get("/search?q=%ED%91%B8%EC%8B%9C&key=" + "t" * 40)
    assert r.status_code == 303 and r.headers["location"] == "/search?q=%ED%91%B8%EC%8B%9C"  # 토큰은 주소에서 지운다
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    c.cookies.set(COOKIE, "t" * 40)
    assert c.get("/").status_code == 200
    evil = TestClient(app, base_url="http://evil.example:8787")  # DNS rebinding
    evil.cookies.set(COOKIE, "t" * 40)
    assert evil.get("/").status_code == 421


class FakeSlack:
    def __init__(self):
        self.messages: list[dict] = []

    async def conversations_history(self, channel, limit, include_all_metadata):
        return {"messages": list(reversed(self.messages))}

    async def chat_postMessage(self, channel, text, metadata):
        self.messages.append({"ts": str(len(self.messages) + 1), "text": text, "metadata": metadata})
        return {"ts": self.messages[-1]["ts"]}

    async def chat_update(self, channel, ts, text, metadata):
        m = next(m for m in self.messages if m["ts"] == ts)
        m.update(text=text, metadata=metadata)


async def test_heartbeat_detects_second_host(tmp_path):
    from askproject.slack.heartbeat import Heartbeat, read_conflict

    slack = FakeSlack()
    a = Heartbeat(slack, "C0HOST", tmp_path / "a")
    b = Heartbeat(slack, "C0HOST", tmp_path / "b")
    await a.beat()
    await a.beat()
    assert len(slack.messages) == 1  # 제자리 갱신 — 채널에 쌓이지 않는다
    assert await a.others() == []
    others = await b.others()
    assert [o["host_name"] for o in others] == [a.host_name]
    sleeps = []

    async def fake_sleep(sec):  # b가 기다리는 동안 a가 꺼진다
        sleeps.append(sec)
        await a.beat("stopped")

    await b.wait_until_alone(sleep=fake_sleep)
    assert sleeps == [60] and read_conflict(tmp_path / "b") is None  # a가 꺼지자 경고를 지우고 시작
    # 같은 데이터 폴더로 다시 시작하면 같은 호스트 ID·메시지를 쓴다
    again = Heartbeat(slack, "C0HOST", tmp_path / "a")
    assert again.host_id == a.host_id and again.ts == a.ts


def test_diagnostics_redacts_secrets(tmp_path, monkeypatch):
    import zipfile

    from askproject import diagnostics

    monkeypatch.delenv("ASKPROJECT_APP", raising=False)
    s = Settings(_env_file=None, data_dir=tmp_path / "d", log_dir=tmp_path / "logs", config_dir=tmp_path / "cfg",
                 slack_bot_token="xoxb-123-456-secret")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "bot.log").write_text("token xoxb-123-456-secret and sk-ant-api03-abc_DEF and "
                                               "Bearer github_pat_11ABC and http://127.0.0.1:8787/?key=" + "k" * 43 + "\n")
    out = diagnostics.build(s, tmp_path / "diag.zip")
    with zipfile.ZipFile(out) as z:
        everything = "".join(z.read(n).decode() for n in z.namelist())
        assert {"paths.json", "config.json", "doctor.txt", "logs/bot.log"} <= set(z.namelist())
    for leaked in ("xoxb-123", "sk-ant-api03", "github_pat_11", "k" * 43):
        assert leaked not in everything
    assert "[가림]" in everything and oct(out.stat().st_mode)[-3:] == "600"


def test_status_for_fresh_install(tmp_path, monkeypatch):
    """첫 실행(레포 미등록)에도 status가 깨지지 않고, 만료·허용 채널·중복 정보를 싣는다."""
    from types import SimpleNamespace

    from askproject import app_status, host

    monkeypatch.setattr(host, "job_states", lambda log_dir: {n: {"state": "off"} for n in ("bot", "console", "backup")})
    s = SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "x.sqlite3", backup_dir=tmp_path, backup_upload_cmd=None,
                        anthropic_api_key=None, llm_provider="api", allowed_channels="", log_dir=tmp_path)
    out = app_status.collect(s, "", "")
    assert out["configured"] is False and out["allowed_channels"] == [] and out["host_conflict"] is None
    assert out["console_key"] is None and out["github_token"] is None and out["protocol"] >= 1


# ---- M4: 마이그레이션 ----

def test_migrate_backs_up_then_upgrades_and_refuses_downgrade(tmp_path, monkeypatch):
    import sqlite3

    from askproject import migrate
    from askproject.store.db import SCHEMA_VERSION, SchemaTooNewError, Store

    monkeypatch.delenv("ASKPROJECT_APP", raising=False)
    s = Settings(_env_file=None, data_dir=tmp_path / "d", config_dir=tmp_path / "cfg", backup_dir=tmp_path / "b")
    s.config_dir.mkdir()
    st = Store(s.db_path)
    st.log_query("옛 질문", "slack", "U", "C", "1", "c1")
    st._conn.execute("PRAGMA user_version = 2")  # 이전 버전 DB인 척
    st._conn.close()

    out = migrate.run(s)
    assert out["schema"] == {"from": 2, "to": SCHEMA_VERSION} and out["backup"]
    assert sqlite3.connect(out["backup"]).execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1  # 백업 먼저
    assert migrate.config_version(s.config_dir) == out["config"]["to"]
    again = migrate.run(s)
    assert again["backup"] is None  # 바꿀 것이 없으면 백업도 안 한다

    c = sqlite3.connect(s.db_path)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")  # 더 새 앱이 만든 DB
    c.close()
    with pytest.raises(migrate.MigrationRefused, match="되돌릴 수 없어요"):
        migrate.run(s)
    with pytest.raises(SchemaTooNewError):
        Store(s.db_path)  # 서비스도 새 DB를 열지 않는다


def test_console_public_host_requires_valid_cloudflare_access_jwt(tmp_path):
    """팀 공개(터널): Host 헤더만으로는 통과 못 하고, Access 팀 키로 서명된 JWT(aud·iss 일치)만 통과"""
    import time as _time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from starlette.testclient import TestClient

    from askproject.console.data import ConsoleData
    from askproject.console.server import CloudflareAccess, create_app
    from askproject.store.db import Store

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = type("J", (), {"get_signing_key_from_jwt": lambda self, t: type("K", (), {"key": key.public_key()})()})()
    cf = CloudflareAccess("team.cloudflareaccess.com", "aud-1", jwk_client=jwks)

    def token(signer=key, aud="aud-1"):
        claims = {"aud": aud, "iss": "https://team.cloudflareaccess.com", "exp": int(_time.time()) + 60,
                  "email": "pm@example.com"}
        return jwt.encode(claims, signer, algorithm="RS256")

    Store(tmp_path / "db.sqlite3")
    data = ConsoleData(tmp_path / "db.sqlite3", "demo", "https://github.com/x/demo", tmp_path)
    app = create_app(data, allowed_hosts={"127.0.0.1:8787"}, token="t" * 40, public_host="ask.example.com", cf_access=cf)
    pub = TestClient(app, base_url="http://ask.example.com")
    assert pub.get("/").status_code == 403  # Host만 맞춰서는 안 된다
    assert pub.get("/", headers={"cf-access-jwt-assertion": token(other)}).status_code == 403  # 다른 키
    assert pub.get("/", headers={"cf-access-jwt-assertion": token(aud="x")}).status_code == 403  # 다른 앱
    assert pub.get("/", headers={"cf-access-jwt-assertion": token()}).status_code == 200
    local = TestClient(app, base_url="http://127.0.0.1:8787")
    assert local.get("/", headers={"cf-access-jwt-assertion": token()}).status_code == 401  # 로컬은 여전히 토큰


def test_version_is_the_same_everywhere():
    """릴리스는 pyproject 버전으로 태그를 붙인다. 앱·백엔드·lock이 다른 버전을 말하면 안 된다."""
    import subprocess
    import sys as _sys
    from pathlib import Path

    from askproject import __version__

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([_sys.executable, str(root / "scripts/bump_version.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == __version__


def test_bump_version_rules(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bump", Path(__file__).resolve().parent.parent / "scripts/bump_version.py")
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    assert bump.next_version("0.1.9", "patch") == "0.1.10"
    assert bump.next_version("0.1.9", "minor") == "0.2.0" and bump.next_version("0.1.9", "major") == "1.0.0"
    assert bump.next_version("0.1.9", "2.0.0") == "2.0.0"
    with pytest.raises(SystemExit):
        bump.next_version("0.1.9", "v2")
