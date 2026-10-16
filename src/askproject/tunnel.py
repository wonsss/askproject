"""웹 콘솔 팀 공유: Cloudflare 대시보드에서 만든 터널(토큰 방식)을 cloudflared로 붙인다.

- 콘솔은 그대로 127.0.0.1:8787에만 열린다. cloudflared가 나가는 연결로 Cloudflare에 붙고, 대시보드의
  Public hostname(askproject.<도메인> → http://127.0.0.1:8787)으로 들어온 요청을 콘솔로 넘긴다.
- 인증은 Cloudflare Access(이메일 로그인)가 터널 앞에서 하고, 콘솔은 Access JWT를 팀 키로 다시 검증한다
  (console/server.py). Access 설정 세 값이 없으면 콘솔이 공개 요청을 막으므로, 터널만 켜져서 열리는 일은 없다.
- 터널 토큰은 비밀값이다(앱 Keychain, 개발 .env). 명령 인자가 아니라 환경변수 TUNNEL_TOKEN으로 넘긴다(ps에 안 보이게).

  askproject tunnel                 launchd 작업(com.askproject.tunnel)이 부른다. 토큰이 없으면 0으로 끝난다
  askproject setup check tunnel     설정·cloudflared·Access 보호 여부를 확인한다(앱 "팀 공유" 화면)
  askproject setup team-domain --host askproject.example.com
                                    Access 로그인으로 넘어가는 주소에서 팀 도메인을 찾는다(사용자가 대시보드에서 찾지 않게)
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
from pathlib import Path

from askproject import paths
from askproject.settings import Settings

log = logging.getLogger("askproject.tunnel")

TEAM_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.cloudflareaccess\.com$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
AUD = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
DASHBOARD = "https://dash.cloudflare.com"
TOKEN_ACCOUNT = "ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN"
# cloudflared에 넘기는 환경변수. 나머지(특히 ASKPROJECT_* 비밀값)는 넘기지 않는다 — cloudflared는 시작할 때
# 환경변수를 로그에 찍는데 TUNNEL_TOKEN만 가린다
CHILD_ENV = ("HOME", "PATH", "TMPDIR", "LANG", "USER", "LOGNAME", "SSL_CERT_FILE")


def _state_path(s: Settings) -> Path:
    """실행 중인 터널의 pid·metrics 주소. 연결 확인이 이 터널의 /ready만 보게 한다(사용자가 따로 띄운 터널과 섞이지 않게)."""
    return s.data_dir / "tunnel.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _running_metrics(s: Settings) -> str | None:
    try:
        state = json.loads(_state_path(s).read_text(encoding="utf-8"))
        os.kill(int(state["pid"]), 0)
        return state["metrics"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parse_token(token: str) -> str:
    """대시보드 터널 토큰(base64 JSON {a: 계정, t: 터널 ID, s: 비밀})을 확인하고 터널 ID를 돌려준다."""
    return token_ids(token)[1]


def token_ids(token: str) -> tuple[str, str]:
    """토큰의 (계정 ID, 터널 ID). 둘 다 비밀이 아니다(비밀은 s) — 대시보드 주소를 만드는 데 쓴다."""
    try:
        raw = json.loads(base64.b64decode(token.strip() + "=" * (-len(token.strip()) % 4), validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ValueError("터널 토큰 형식이 아니에요 — 대시보드의 설치 명령에서 'eyJ'로 시작하는 부분만 복사하세요") from None
    if not (isinstance(raw, dict) and raw.get("a") and raw.get("s") and UUID.match(str(raw.get("t", "")))):
        raise ValueError("터널 토큰에 계정·터널 ID·비밀이 없어요 — 대시보드에서 토큰을 다시 복사하세요")
    return str(raw["a"]), raw["t"]


def dashboard_info(token: str | None) -> dict | None:
    """앱이 이 터널의 대시보드 화면(Routes)을 바로 열 수 있게 하는 비밀 아닌 정보. 토큰이 없거나 형식이 다르면 None."""
    if not token:
        return None
    try:
        account, tunnel_id = token_ids(token)
    except ValueError:
        return None
    if not ACCOUNT.match(account):  # 주소에 넣으므로 형식이 다르면 만들지 않는다
        return None
    base = f"{DASHBOARD}/{account}/tunnels/{tunnel_id}"
    return {"account_id": account, "tunnel_id": tunnel_id, "routes_url": f"{base}/routes"}


def cloudflared_path() -> str | None:
    """cloudflared: ASKPROJECT_CLOUDFLARED_PATH → 번들 내장 → PATH → Homebrew 순."""
    explicit = os.environ.get("ASKPROJECT_CLOUDFLARED_PATH")
    if explicit:
        return explicit
    b = paths.bundle_dir()
    if b and (b / "bin" / "cloudflared").is_file():
        return str(b / "bin" / "cloudflared")
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
        if Path(p).is_file():
            return p
    return None


def missing(s: Settings) -> list[str]:
    """팀 공유에 필요한데 비어 있는 항목(화면 표시 이름)."""
    need = {"공개 주소": s.console_public_host, "Access 팀 도메인": s.cf_access_team_domain,
            "Access AUD 태그": s.cf_access_aud, "터널 토큰": s.cloudflare_tunnel_token}
    return [name for name, v in need.items() if not v]


def run(s: Settings) -> int:
    """cloudflared를 띄우고 출력을 로그로 옮긴다. launchd가 보내는 SIGTERM은 cloudflared에 넘긴다."""
    # 토큰만 있으면 띄운다. 대시보드는 터널이 연결돼야 다음 단계(공개 주소·Access 앱)로 넘어가기 때문이다.
    # Access 값이 아직 없어도 안전하다 — 콘솔은 공개 주소로 온 요청을 Access JWT 없이는 받지 않고(console/server.py),
    # 공개 주소 설정 전에는 Host가 127.0.0.1이 아니라 421로 거절한다.
    if not s.cloudflare_tunnel_token:
        # KeepAlive(SuccessfulExit=false)라 0으로 끝나면 다시 띄우지 않는다
        print("터널 토큰이 없어 터널을 띄우지 않았어요 — 설정 → 팀 공유 2번에 붙여넣고 저장하세요")
        return 0
    exe = cloudflared_path()
    if not exe:
        log.error("cloudflared를 찾지 못했어요 — 앱을 다시 설치하거나 `brew install cloudflared`")
        return 1
    token = s.cloudflare_tunnel_token.get_secret_value()
    tunnel_id = parse_token(token)
    env = {k: os.environ[k] for k in CHILD_ENV if k in os.environ}
    env["TUNNEL_TOKEN"] = token
    # --no-autoupdate: 번들 안의 서명된 바이너리를 스스로 바꾸지 않는다(앱 업데이트로만 바뀐다)
    # --config: 빈 설정 파일을 준다. 없으면 ~/.cloudflared/config.yml(사용자가 따로 쓰는 터널)을 읽어 설정이 섞인다
    empty = s.data_dir / "cloudflared.yml"
    paths.atomic_write(empty, "{}\n")
    # --metrics: 고정 포트(기본 20241~)는 사용자가 따로 띄운 cloudflared와 겹치므로 빈 포트를 골라 상태 파일에 적는다
    metrics = f"127.0.0.1:{_free_port()}"
    cmd = [exe, "tunnel", "--config", str(empty), "--no-autoupdate", "--loglevel", "info", "--metrics", metrics, "run"]
    log.info("터널 %s… → https://%s (cloudflared %s, metrics %s)", tunnel_id[:8],
             s.console_public_host or "<공개 주소 미설정>", exe, metrics)
    if missing(s):
        log.info("아직 비어 있음: %s — 채우기 전까지 콘솔은 공개 주소 요청을 거절해요", ", ".join(missing(s)))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    paths.atomic_write(_state_path(s), json.dumps({"pid": proc.pid, "metrics": metrics}))

    def stop(signum, _frame):
        proc.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            log.info("cloudflared: %s", line.rstrip()[:500])
        return proc.wait()
    finally:
        _state_path(s).unlink(missing_ok=True)


def _access_team(location: str) -> str | None:
    """Access 로그인 리다이렉트(https://<팀>.cloudflareaccess.com/cdn-cgi/access/login/…)의 팀 도메인."""
    from urllib.parse import urlsplit

    host = (urlsplit(location).hostname or "").lower()
    return host if TEAM_DOMAIN.match(host) else None


def detect_team(host: str, client=None) -> dict:
    """공개 주소를 로그인 없이 열어 Access가 보내는 로그인 주소에서 팀 도메인을 읽는다."""
    import httpx

    host = host.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not HOSTNAME.match(host):
        return {"ok": False, "detail": "공개 주소를 먼저 넣어 주세요 (예: askproject.example.com)"}
    c = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        r = c.get(f"https://{host}/")
    except httpx.HTTPError:
        return {"ok": False, "detail": f"{host}에 연결하지 못했어요 — 3번(공개 주소)을 먼저 마치세요"}
    finally:
        if client is None:
            c.close()
    team = _access_team(r.headers.get("location", ""))
    if team:
        return {"ok": True, "team_domain": team, "detail": f"팀 도메인을 찾았어요: {team}"}
    return {"ok": False, "detail": f"{host}가 Access 로그인으로 넘어가지 않아요(HTTP {r.status_code}) — 4번(Access 앱)을 먼저 마치세요"}


def check(s: Settings, client=None) -> dict:
    """설정 → 토큰 → cloudflared → 터널 연결(/ready) → Access 팀 키 → 공개 주소가 Access 로그인으로 넘어가는지 순서로 본다."""
    import httpx

    def fail(detail: str) -> dict:
        return {"ok": False, "step": "tunnel", "detail": detail}

    lack = missing(s)
    if lack:
        return fail(f"비어 있는 항목: {', '.join(lack)}")
    try:
        parse_token(s.cloudflare_tunnel_token.get_secret_value())
    except ValueError as exc:
        return fail(str(exc))
    if not cloudflared_path():
        return fail("cloudflared를 찾지 못했어요 — 앱을 다시 설치하세요 (개발 모드는 brew install cloudflared)")
    host, team = s.console_public_host, s.cf_access_team_domain
    c = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        metrics, connections = _running_metrics(s), 0
        if metrics:
            try:
                ready = c.get(f"http://{metrics}/ready")
                connections = ready.json().get("readyConnections", 0) if ready.status_code == 200 else 0
            except (httpx.HTTPError, ValueError):
                pass
        if not connections:
            return fail("터널이 이 맥에서 Cloudflare에 연결되지 않았어요 — 팀 공유를 켜고 30초 뒤 다시 확인하세요 "
                        "(로그: tunnel.log)")
        try:
            certs = c.get(f"https://{team}/cdn-cgi/access/certs")
            if certs.status_code != 200 or not certs.json().get("keys"):
                return fail(f"Access 팀 도메인({team})의 공개 키를 못 받았어요 — Zero Trust → Settings의 팀 이름을 확인하세요")
        except (httpx.HTTPError, ValueError):
            return fail(f"Access 팀 도메인({team})에 연결하지 못했어요 — 팀 이름을 확인하세요")
        try:
            r = c.get(f"https://{host}/")
        except httpx.HTTPError:
            return fail(f"{host}에 연결하지 못했어요 — 터널의 Public hostname과 도메인이 Cloudflare에 있는지 확인하세요")
    finally:
        if client is None:
            c.close()
    location = r.headers.get("location", "")
    seen_team = _access_team(location)
    if seen_team and seen_team != team:
        return fail(f"팀 도메인이 달라요 — 저장한 값은 {team}, 이 주소의 Access는 {seen_team}이에요")
    if r.status_code in (301, 302, 303, 307) and ("cloudflareaccess.com" in location or "/cdn-cgi/access/" in location):
        return {"ok": True, "step": "tunnel",
                "detail": f"https://{host} — 터널 연결({connections}개)·Access 로그인 보호 확인. 팀원은 허용 목록의 이메일로 들어와요"}
    if r.status_code in (502, 530):
        return fail(f"{host}가 이 터널로 연결되지 않아요(HTTP {r.status_code}) — 대시보드 터널의 Public hostname이 "
                    "이 주소이고 서비스가 http://127.0.0.1:8787인지 확인하세요")
    # 200·403 등: Cloudflare Access를 거치지 않고 응답이 왔다. 콘솔이 JWT 없는 요청은 막지만, 앞단 보호가 빠진 상태다
    return fail(f"{host}가 Access 로그인 없이 응답해요(HTTP {r.status_code}) — Zero Trust → Access → Applications에 "
                "이 주소로 앱을 만들고 Allow 정책에 팀원 이메일을 넣으세요")
