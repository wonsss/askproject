import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from askproject import host
from askproject.host import acquire_serve_lock, jobs, plist_for

BUNDLE_AGENTS = Path(__file__).parent.parent / "macos" / "LaunchAgents"


def test_serve_lock_allows_only_one(tmp_path):
    first = acquire_serve_lock(tmp_path)
    assert first is not None
    assert acquire_serve_lock(tmp_path) is None  # 두 번째 봇은 거절
    first.close()
    again = acquire_serve_lock(tmp_path)  # 첫 봇이 끝나면 다시 띄울 수 있다
    assert again is not None
    again.close()


def test_launchd_plists(tmp_path):
    repo, logs = Path("/repo/askproject"), tmp_path / "logs"
    js = {j.name: j for j in jobs(repo, "/opt/homebrew/bin/uv", 8787)}
    bot = plist_for(js["bot"], repo, logs)
    assert bot["ProgramArguments"][0] == "/opt/homebrew/bin/uv"  # 잠자기 방지는 serve가 스스로 건다(hold_awake)
    assert bot["ProgramArguments"][-1] == "serve" and "--project" in bot["ProgramArguments"]
    assert bot["KeepAlive"] is True and bot["ThrottleInterval"] == 60 and bot["RunAtLoad"] is True
    assert bot["WorkingDirectory"] == str(repo)  # .env를 레포에서 읽는다
    assert not any(k.startswith("ANTHROPIC") for k in bot["EnvironmentVariables"])  # 셸 키가 끼어들지 않게
    console = plist_for(js["console"], repo, logs)
    assert console["ProgramArguments"][-3:] == ["console", "--port", "8787"] and "caffeinate" not in console["ProgramArguments"][0]
    backup = plist_for(js["backup"], repo, logs)
    assert backup["StartCalendarInterval"] == {"Hour": 3, "Minute": 30} and "KeepAlive" not in backup
    assert backup["ProgramArguments"][-1] == "nightly"  # 백업 → 복구 확인 → 정리를 한 프로세스에서


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("plutil"), reason="macOS 전용")
def test_plists_pass_plutil_lint(tmp_path):
    repo = Path("/repo/askproject")
    for job in jobs(repo, "/opt/homebrew/bin/uv"):
        f = tmp_path / f"{job.label}.plist"
        f.write_bytes(plistlib.dumps(plist_for(job, repo, tmp_path)))
        r = subprocess.run(["plutil", "-lint", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout


def test_hold_awake_ties_caffeinate_to_bot_pid(monkeypatch):
    calls = []
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host.subprocess, "Popen", lambda args, **kw: calls.append(args) or "proc")
    assert host.hold_awake("default") is None and not calls  # 기본: 맥과 같이 잠듦
    assert host.hold_awake("lid-closed") == "proc"
    assert calls[0][:2] == ["/usr/bin/caffeinate", "-s"] and calls[0][-2] == "-w"  # 봇이 죽으면 같이 풀림


def test_bundle_agent_plists_are_relocatable():
    """앱 번들 plist: 절대 경로 없이 BundleProgram(번들 상대)만 — 앱을 옮기거나 업데이트해도 경로가 깨지지 않게"""
    found = {p.stem: plistlib.loads(p.read_bytes()) for p in BUNDLE_AGENTS.glob("*.plist")}
    assert set(found) == {"com.askproject.bot", "com.askproject.console", "com.askproject.backup", "com.askproject.tunnel"}
    for label, d in found.items():
        assert d["Label"] == label and d["BundleProgram"] == "Contents/MacOS/askproject-backend"
        assert not any(str(a).startswith("/") for a in d["ProgramArguments"])
        assert "StandardOutPath" not in d  # 로그는 백엔드가 ~/Library/Logs로 (logs.py)
        assert d["EnvironmentVariables"]["ASKPROJECT_JOB"] == label.rsplit(".", 1)[1]
    assert found["com.askproject.bot"]["KeepAlive"] is True
    assert found["com.askproject.backup"]["ProgramArguments"][-1] == "nightly"
    # 팀 공유 터널: 설정이 없어 0으로 끝나면 다시 띄우지 않는다(실패일 때만 재시작)
    assert found["com.askproject.tunnel"]["KeepAlive"] == {"SuccessfulExit": False}
