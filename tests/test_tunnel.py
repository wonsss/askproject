"""웹 콘솔 팀 공유: 터널 토큰·설정 검증, 설정이 없으면 터널을 띄우지 않음, 연결 확인의 판정."""

import base64
import json
import os

import httpx
import pytest

from askproject import config_store, diagnostics, keychain, tunnel
from askproject.settings import Settings

TUNNEL_ID = "6ff42ae2-765d-4adf-8112-31c55c1551ef"
ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
AUD = "a" * 64


def _token(**over) -> str:
    body = {"a": ACCOUNT_ID, "t": TUNNEL_ID, "s": "c2VjcmV0", **over}
    return base64.b64encode(json.dumps(body).encode()).decode().rstrip("=")


@pytest.fixture
def app_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKPROJECT_APP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "ASKPROJECT_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    store: dict[str, str] = {}
    monkeypatch.setattr(keychain, "get", lambda a: store.get(a))
    monkeypatch.setattr(keychain, "put", lambda a, v: store.__setitem__(a, v))
    monkeypatch.setattr(keychain, "delete", lambda a: store.pop(a, None))
    return store


def _shared(**over) -> Settings:
    values = {"console_public_host": "ask.example.com", "cf_access_team_domain": "acme.cloudflareaccess.com",
              "cf_access_aud": AUD, "cloudflare_tunnel_token": _token(), **over}
    return Settings(_env_file=None, **values)


def test_dashboard_info_needs_valid_ids():
    assert tunnel.dashboard_info(None) is None
    assert tunnel.dashboard_info("eyJ%%%") is None
    assert tunnel.dashboard_info(_token(a="../../evil")) is None  # 주소에 들어가므로 형식 검사


def test_parse_token():
    assert tunnel.parse_token(_token()) == TUNNEL_ID
    with pytest.raises(ValueError, match="형식"):
        tunnel.parse_token("eyJ%%%")
    with pytest.raises(ValueError, match="터널 ID"):
        tunnel.parse_token(_token(t="not-a-uuid"))


def test_config_set_share_settings_and_token_to_keychain(app_home):
    s = Settings()
    patch = {"settings": {"console_public_host": "https://Ask.Example.com/", "cf_access_team_domain": "acme.cloudflareaccess.com",
                          "cf_access_aud": AUD},
             "secrets": {"ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN": _token()}}
    config_store.set_from_json(s, json.dumps(patch))
    assert app_home["ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN"] == _token()
    env = s.env_path.read_text()
    assert "ASKPROJECT_CONSOLE_PUBLIC_HOST=ask.example.com" in env and "eyJ" not in env  # 주소는 정규화, 토큰은 파일에 없음
    got = config_store.get(Settings())
    assert got["secrets"]["ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN"] is True
    assert got["settings"]["cf_access_aud"] == AUD and "eyJ" not in json.dumps(got)
    assert got["tunnel"]["routes_url"] == f"https://dash.cloudflare.com/{ACCOUNT_ID}/tunnels/{TUNNEL_ID}/routes"
    assert "c2VjcmV0" not in json.dumps(got)  # 토큰의 비밀(s)은 내보내지 않는다


@pytest.mark.parametrize("patch, msg", [
    ({"settings": {"console_public_host": "ask example"}}, "호스트 이름"),
    ({"settings": {"cf_access_team_domain": "acme.example.com"}}, "cloudflareaccess.com"),
    ({"settings": {"cf_access_aud": "abc"}}, "64자리"),
    ({"secrets": {"ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN": "cloudflared service install"}}, "eyJ"),
    ({"secrets": {"ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN": _token(s="")}}, "토큰"),
])
def test_config_set_rejects_bad_share_values(app_home, patch, msg):
    with pytest.raises(Exception, match=msg):
        config_store.set_from_json(Settings(), json.dumps(patch))


def test_run_without_token_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *a, **k: pytest.fail("터널을 띄우면 안 된다"))
    assert tunnel.run(Settings(_env_file=None, console_public_host="ask.example.com")) == 0
    assert "터널 토큰" in capsys.readouterr().out


def test_run_with_token_only(monkeypatch, tmp_path):
    """대시보드는 터널이 연결돼야 공개 주소·Access 단계로 넘어간다 — 토큰만으로 띄운다."""
    started = []

    class Proc:
        pid, stdout = 1, iter([])

        def wait(self):
            return 0

    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda cmd, **kw: started.append(cmd) or Proc())
    monkeypatch.setattr(tunnel, "cloudflared_path", lambda: "/bin/cloudflared")
    assert tunnel.run(Settings(_env_file=None, data_dir=tmp_path, cloudflare_tunnel_token=_token())) == 0
    assert started


def test_console_rejects_tunnel_requests_before_access_is_configured(tmp_path):
    """Access 값이 없을 때 터널로 들어온 요청(Host가 공개 주소)은 콘솔이 받지 않는다."""
    from starlette.testclient import TestClient

    from askproject.console.data import ConsoleData
    from askproject.console.server import create_app
    from askproject.store.db import Store

    Store(tmp_path / "db.sqlite3")
    data = ConsoleData(tmp_path / "db.sqlite3", "demo", "https://github.com/x/demo", tmp_path)
    app = create_app(data, allowed_hosts={"127.0.0.1:8787"}, token="t" * 40)  # public_host·cf_access 없음
    r = TestClient(app, base_url="https://ask.example.com").get("/?key=" + "t" * 40)  # 토큰을 알아도
    assert r.status_code == 421


def test_run_passes_token_by_env_and_isolates_config(monkeypatch, tmp_path):
    seen = {}

    class Proc:
        pid = 4321
        stdout = iter(["INF Registered tunnel connection\n"])

        def wait(self):
            return 0

    def popen(cmd, env, **kw):
        seen.update(cmd=cmd, env=env)
        return Proc()

    monkeypatch.setattr(tunnel.subprocess, "Popen", popen)
    monkeypatch.setattr(tunnel, "cloudflared_path", lambda: "/bin/cloudflared")
    monkeypatch.setenv("ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN", _token())
    assert tunnel.run(_shared(data_dir=tmp_path)) == 0
    assert seen["env"]["TUNNEL_TOKEN"] == _token() and _token() not in " ".join(seen["cmd"])  # ps에 안 보이게
    # cloudflared는 환경변수를 로그에 찍고 TUNNEL_TOKEN만 가린다 — 다른 이름의 비밀값은 넘기지 않는다
    assert set(seen["env"]) <= set(tunnel.CHILD_ENV) | {"TUNNEL_TOKEN"}
    metrics = seen["cmd"][seen["cmd"].index("--metrics") + 1]
    assert metrics.startswith("127.0.0.1:") and not (tmp_path / "tunnel.json").exists()  # 끝나면 상태 파일을 지운다
    assert seen["cmd"][seen["cmd"].index("--config") + 1] == str(tmp_path / "cloudflared.yml")  # ~/.cloudflared를 안 읽음
    assert "--no-autoupdate" in seen["cmd"]


def _client(ready=4, site=(302, {"location": "https://acme.cloudflareaccess.com/cdn-cgi/access/login/ask.example.com"})):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/ready":
            return httpx.Response(200, json={"status": 200, "readyConnections": ready})
        if req.url.path == "/cdn-cgi/access/certs":
            return httpx.Response(200, json={"keys": [{"kid": "k"}]})
        return httpx.Response(site[0], headers=site[1])
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("ready, site, ok, msg", [
    (4, (302, {"location": "https://acme.cloudflareaccess.com/cdn-cgi/access/login/x"}), True, "보호"),
    (0, (302, {"location": "https://acme.cloudflareaccess.com/"}), False, "연결되지 않았어요"),
    (4, (403, {}), False, "Access 로그인 없이"),  # 앞단 Access가 빠졌다 — 콘솔이 막아도 실패로 알린다
    (4, (530, {}), False, "Public hostname"),
])
def test_check_verdicts(monkeypatch, tmp_path, ready, site, ok, msg):
    monkeypatch.setattr(tunnel, "cloudflared_path", lambda: "/bin/cloudflared")
    s = _shared(data_dir=tmp_path)
    (tmp_path / "tunnel.json").write_text(json.dumps({"pid": os.getpid(), "metrics": "127.0.0.1:1"}))
    r = tunnel.check(s, client=_client(ready, site))
    assert r["ok"] is ok and msg in r["detail"] and r["step"] == "tunnel"


def test_check_ignores_other_cloudflared_without_our_state_file(monkeypatch, tmp_path):
    """사용자가 따로 띄운 cloudflared의 /ready가 응답해도, 이 앱의 터널 상태 파일이 없으면 연결 안 됨으로 본다."""
    monkeypatch.setattr(tunnel, "cloudflared_path", lambda: "/bin/cloudflared")
    r = tunnel.check(_shared(data_dir=tmp_path), client=_client(4))
    assert not r["ok"] and "연결되지 않았어요" in r["detail"]


def test_check_reports_team_domain_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(tunnel, "cloudflared_path", lambda: "/bin/cloudflared")
    s = _shared(data_dir=tmp_path, cf_access_team_domain="other.cloudflareaccess.com")
    (tmp_path / "tunnel.json").write_text(json.dumps({"pid": os.getpid(), "metrics": "127.0.0.1:1"}))
    r = tunnel.check(s, client=_client())
    assert not r["ok"] and "acme.cloudflareaccess.com" in r["detail"]


@pytest.mark.parametrize("site, ok, want", [
    ((302, {"location": "https://acme.cloudflareaccess.com/cdn-cgi/access/login/ask.example.com?kid=1"}), True, "acme.cloudflareaccess.com"),
    ((302, {"location": "https://evil.example.com/cloudflareaccess.com"}), False, "4번"),  # 호스트가 팀 도메인일 때만
    ((200, {}), False, "4번"),
])
def test_detect_team(site, ok, want):
    r = tunnel.detect_team("https://Ask.Example.com/", client=_client(site=site))
    assert r["ok"] is ok and want in (r.get("team_domain") or r["detail"])


def test_detect_team_needs_host():
    assert not tunnel.detect_team("")["ok"]


def test_check_lists_missing_items():
    r = tunnel.check(Settings(_env_file=None, console_public_host="ask.example.com"))
    assert not r["ok"] and "AUD" in r["detail"] and "터널 토큰" in r["detail"]


def test_diagnostics_redacts_tunnel_token_and_jwt():
    text = diagnostics.redact(f"token={_token()} jwt=eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig")
    assert "eyJ" not in text
