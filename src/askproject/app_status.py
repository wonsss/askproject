"""메뉴 막대 앱이 읽는 상태 한 덩어리(JSON). 앱은 화면만 맡고, 판단은 여기 한곳에서 한다.

기본은 Claude를 호출하지 않는다(비용 0). check_claude=True일 때만 1토큰 호출로 키·크레딧을 확인한다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from askproject import host
from askproject.paths import eval_results_dir
from askproject.ops import latest_backup, offsite


def _console_info(data_dir: Path) -> dict | None:
    """실행 중인 콘솔의 포트·토큰(data/console.json). 프로세스가 없으면 None."""
    import json
    import os

    try:
        info = json.loads((data_dir / "console.json").read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        return info
    except (OSError, ValueError, KeyError):
        return None


def _midnight() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, t.tm_wday, t.tm_yday, t.tm_isdst))


def collect(settings, repo_name: str, web_base: str, port: int | None = None,
            check_claude: bool = False, repos=None) -> dict:
    from askproject.console.data import ROUTE_KO, ConsoleData

    from askproject import __protocol__, __version__, paths
    from askproject.indexing.repo_sync import github_error_status, token_expiry_status
    from askproject.slack.heartbeat import read_conflict

    jobs = host.job_states(getattr(settings, "log_dir", settings.data_dir / "logs"))
    provider = getattr(settings, "llm_provider", "api")
    console = _console_info(settings.data_dir)
    port = port or host.preferred_console_port(settings)  # 콘솔이 꺼져 있을 때 보여 줄 주소
    if console:
        port = console.get("port", port)  # 사용 중인 포트를 피해 옮겼으면 실제 포트
    allowed = getattr(settings, "allowed_channels", "")
    out: dict = {
        "generated_at": time.time(),
        "version": __version__,
        "protocol": __protocol__,
        "mode": "app" if paths.app_mode() else "dev",
        "jobs": jobs,
        "installed": any(j["state"] != "off" for j in jobs.values()),
        "configured": bool(repo_name),
        "console_url": f"http://127.0.0.1:{port}",
        # 실행당 토큰. 앱은 열 주소 끝에 ?key=를 붙인다(콘솔이 쿠키로 바꾸고 주소에서 지운다)
        "console_key": console["token"] if console else None,
        # 팀 공유 공개 주소(Cloudflare Access 로그인을 거친다). 설정하지 않았으면 None
        "console_public_url": f"https://{public}" if (public := getattr(settings, "console_public_host", None)) else None,
        "github_token": token_expiry_status(settings.data_dir),
        "github_error": github_error_status(settings.data_dir),  # 마지막 GitHub 호출이 거부됐으면 원인
        "allowed_channels": [c.strip() for c in allowed.split(",") if c.strip()],
        "host_conflict": read_conflict(settings.data_dir),
        "provider": provider,
        "auth": "api_key" if settings.anthropic_api_key else "missing",
        "power": host.power_info(getattr(settings, "power_mode", "lid-open")) if sys.platform == "darwin" else None,
    }
    if provider == "subscription":
        from askproject.llm.claude_cli import find_claude

        out["claude_cli"] = bool(find_claude())
        out["auth"] = "subscription" if out["claude_cli"] else "cli_missing"
    if settings.db_path.exists() and repo_name:
        data = ConsoleData(settings.db_path, repo_name, web_base, eval_results_dir(), repos=repos)
        today, week = _midnight(), time.time() - 7 * 86400
        rows_week = data.rows(source="all", since=week)
        slack_today = [r for r in rows_week if r.source == "slack" and r.ts >= today]
        out["today"] = {"questions": len(slack_today),
                        "cost": round(sum(r.cost for r in rows_week if r.ts >= today), 4)}
        out["week"] = {"questions": sum(1 for r in rows_week if r.source == "slack"),
                       "cost": round(sum(r.cost for r in rows_week), 4)}
        out["check"] = len(data.needs_check())
        out["recent"] = [{"id": r.id, "question": r.question, "route": ROUTE_KO.get(r.route or "", r.route or ""),
                          "provider": r.provider, "confidence": r.confidence, "ts": r.ts, "cost": round(r.cost, 4),
                          "flags": r.check_reasons}
                         for r in data.rows(source="slack")[:6]]
        data.conn.close()
    else:
        out.update({"today": {"questions": 0, "cost": 0}, "week": {"questions": 0, "cost": 0}, "check": 0, "recent": []})
    b = latest_backup(settings.backup_dir)
    out["last_backup"] = ({"name": b.name, "age_hours": round((time.time() - b.stat().st_mtime) / 3600, 1),
                           "offsite": offsite(settings, b)} if b else None)
    if check_claude:
        from askproject.doctor import claude_preflight

        problem = claude_preflight(settings)
        out["claude"] = {"ok": problem is None, "problem": problem}
    return out
