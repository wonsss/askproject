"""첫 실행 마법사가 부르는 점검·조회(`askproject setup …`, 모두 JSON 출력). 토큰을 쓰지 않는 호출만 한다.

- manifest-url   슬랙 앱 만들기 링크(매니페스트를 미리 채움)
- pat-url        GitHub fine-grained PAT 만들기 링크(이름·설명·만료·contents=read를 미리 채움)
- check slack|github|claude|tunnel   저장된 값으로 연결 확인(tunnel: 웹 콘솔 팀 공유, askproject.tunnel) → {"ok", "detail", "step"}
- repos          PAT로 읽을 수 있는 레포 목록(선택용)
- channels       봇이 들어가 있는 공개 채널 목록(허용 목록 선택용)
- team-domain --host H   공개 주소의 Access 로그인 리다이렉트에서 팀 도메인 찾기(팀 공유)
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

from askproject.settings import Settings

MANIFEST = Path(__file__).parent / "defaults" / "slack-app-manifest.yaml"
PAT_EXPIRES_DAYS = 90


def manifest_url() -> str:
    """api.slack.com/apps?new_app=1&manifest_json=… — 붙여넣기 없이 매니페스트가 채워진 생성 화면이 열린다."""
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    body = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return f"https://api.slack.com/apps?new_app=1&manifest_json={quote(body, safe='')}"


def pat_url(owner: str | None = None) -> str:
    """GitHub 문서의 fine-grained PAT 템플릿 파라미터. 레포 선택은 미리 채울 수 없어 사용자가 고른다."""
    params = {"name": "AskProject", "description": "AskProject 봇 — 레포 내용 읽기 전용",
              "expires_in": str(PAT_EXPIRES_DAYS), "contents": "read"}
    if owner:
        params["target_name"] = owner
    return "https://github.com/settings/personal-access-tokens/new?" + urlencode(params)


def check_slack(s: Settings) -> dict:
    if not (s.slack_bot_token and s.slack_app_token):
        return {"ok": False, "step": "slack", "detail": "봇 토큰(xoxb-)과 앱 토큰(xapp-)을 모두 넣어 주세요"}
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    try:
        r = WebClient(token=s.slack_bot_token.get_secret_value()).auth_test()
    except SlackApiError as exc:
        err = exc.response.get("error", "")
        hint = {"invalid_auth": "봇 토큰이 맞지 않아요 — Install App 화면의 Bot User OAuth Token(xoxb-)을 복사하세요",
                "not_authed": "봇 토큰이 비어 있어요",
                "account_inactive": "앱이 워크스페이스에서 제거됐어요 — 다시 설치하세요"}.get(err, err)
        return {"ok": False, "step": "slack", "detail": hint}
    try:
        # 앱 토큰(xapp-, connections:write)으로 Socket Mode 연결 URL을 받아 본다(실제 연결은 하지 않는다)
        WebClient().apps_connections_open(app_token=s.slack_app_token.get_secret_value())
    except SlackApiError as exc:
        err = exc.response.get("error", "")
        return {"ok": False, "step": "slack",
                "detail": "앱 토큰(xapp-)이 맞지 않거나 connections:write 범위가 없어요 — Basic Information → "
                          f"App-Level Tokens에서 다시 만드세요 ({err})"}
    return {"ok": True, "step": "slack", "detail": f"{r['team']} 워크스페이스 · 봇 {r['user']}", "team": r["team"]}


def check_github(s: Settings) -> dict:
    from askproject.config import load_repos
    from askproject.indexing.repo_sync import RepoSync

    if not s.github_token and not s.github_app_id:
        return {"ok": False, "step": "github", "detail": "GitHub 토큰을 넣어 주세요"}
    try:
        repos = load_repos(s.config_dir)
    except ValueError:
        return {"ok": False, "step": "repos", "detail": "읽을 레포를 골라 주세요"}
    sync = RepoSync(s)
    try:
        sha = sync.check_access(repos[0])
    except Exception as exc:
        return {"ok": False, "step": "github", "detail": str(exc)[:200]}
    return {"ok": True, "step": "github", "detail": f"{repos[0].name}@{repos[0].ref} 접근 가능 ({sha})"}


def check_claude(s: Settings) -> dict:
    from askproject.doctor import claude_preflight

    problem = claude_preflight(s)  # 1토큰 호출(약 $0.00001)
    return {"ok": problem is None, "step": "claude", "detail": problem or "API 키·크레딧 정상"}


def list_repos(s: Settings) -> dict:
    """PAT가 읽을 수 있는 레포. fine-grained PAT는 고른 레포만 돌려준다."""
    from askproject.indexing.repo_sync import GitHubAuth, record_token_expiry

    import httpx

    auth = GitHubAuth(s)
    if auth.kind == "none":
        return {"ok": False, "detail": "GitHub 토큰을 먼저 넣어 주세요", "repos": []}
    out, url = [], f"{s.github_api_url}/user/repos?per_page=100&sort=pushed"
    with httpx.Client(timeout=30) as c:
        for _ in range(5):  # 최대 500개
            r = c.get(url, headers=auth.headers())
            if r.status_code == 401:
                return {"ok": False, "detail": "토큰이 거부됐어요(만료·폐기 확인)", "repos": []}
            r.raise_for_status()
            if auth.kind == "token":
                record_token_expiry(s.data_dir, r)
            out += [{"full_name": x["full_name"], "name": x["name"], "url": x["html_url"],
                     "default_branch": x.get("default_branch") or "main", "private": x.get("private", False),
                     "description": x.get("description") or ""} for x in r.json()]
            nxt = r.links.get("next", {}).get("url")
            if not nxt:
                break
            url = nxt
    detail = f"{len(out)}개" if out else ("읽을 수 있는 레포가 없어요 — 토큰을 만들 때 레포를 선택했는지, "
                                         "조직이 토큰 승인을 요구하는지 확인하세요")
    return {"ok": bool(out), "detail": detail, "repos": out}


def list_channels(s: Settings) -> dict:
    if not s.slack_bot_token:
        return {"ok": False, "detail": "슬랙 봇 토큰을 먼저 넣어 주세요", "channels": []}
    from slack_sdk import WebClient

    client = WebClient(token=s.slack_bot_token.get_secret_value())
    chans, cursor = [], None
    while True:
        r = client.users_conversations(types="public_channel", exclude_archived=True, limit=200, cursor=cursor)
        chans += [{"id": c["id"], "name": c["name"]} for c in r["channels"]]
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    detail = f"{len(chans)}개" if chans else "봇이 들어간 채널이 없어요 — 채널에서 /invite @AskProject 후 새로고침"
    return {"ok": bool(chans), "detail": detail, "channels": sorted(chans, key=lambda c: c["name"])}


def run(action: str, target: str | None = None, owner: str | None = None, host: str | None = None) -> dict:
    if action == "team-domain":
        from askproject.tunnel import detect_team

        return detect_team(host or "")
    if action == "manifest-url":
        return {"url": manifest_url()}
    if action == "pat-url":
        return {"url": pat_url(owner)}
    s = Settings()
    if action == "check":
        from askproject.tunnel import check as check_tunnel

        fn = {"slack": check_slack, "github": check_github, "claude": check_claude, "tunnel": check_tunnel}[target or ""]
        try:
            return fn(s)
        except Exception as exc:
            return {"ok": False, "step": target, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if action == "repos":
        return list_repos(s)
    if action == "channels":
        return list_channels(s)
    raise ValueError(action)
