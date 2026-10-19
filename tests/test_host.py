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
    default = {j.name: j for j in jobs(repo, "/opt/homebrew/bin/uv")}["console"]
    assert default.args[-1] == "console"  # 포트를 박지 않으면 콘솔이 실행할 때 정한다
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


@pytest.fixture
def fake_launchd(tmp_path, monkeypatch):
    """실제 launchd를 건드리지 않는다: LaunchAgents 폴더는 tmp, launchctl 호출은 기록만."""
    calls, loaded = [], set()

    def launchctl(*args):
        calls.append(args)
        if args[0] == "bootout":
            loaded.discard(args[1].rsplit("/", 1)[1])
        if args[0] == "bootstrap":
            loaded.add(Path(args[2]).stem)
        ok = args[0] != "print" or args[1].rsplit("/", 1)[1] in loaded
        out = "\tpid = 4242\n" if args[0] == "print" and ok and "waiting" not in loaded else ""
        return subprocess.CompletedProcess(args, 0 if ok else 113, out, "")

    monkeypatch.setattr(host, "_agents_dir", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(host, "_launchctl", launchctl)
    (tmp_path / "LaunchAgents").mkdir()
    return calls, loaded, tmp_path / "LaunchAgents"


def _register(agents: Path, repo: Path) -> None:
    job = next(j for j in jobs(repo, "/opt/homebrew/bin/uv") if j.name == "bot")
    (agents / f"{job.label}.plist").write_bytes(plistlib.dumps(plist_for(job, repo, repo / "logs")))


def test_restart_from_same_repo_kickstarts_without_rewriting(fake_launchd, tmp_path):
    calls, loaded, agents = fake_launchd
    repo = tmp_path / "main"
    repo.mkdir()
    _register(agents, repo)
    loaded.add("com.askproject.bot")
    before = (agents / "com.askproject.bot.plist").read_bytes()
    assert host.restart_from("bot", repo) == ("restarted", repo)
    assert ("kickstart", "-k", f"{host._domain()}/com.askproject.bot") in calls
    assert not any(c[0] in ("bootout", "bootstrap") for c in calls)  # 재등록하지 않는다
    assert (agents / "com.askproject.bot.plist").read_bytes() == before


def test_restart_from_other_checkout_leaves_bot_alone(fake_launchd, tmp_path):
    """worktree로 빌드한 개발 앱이 설정을 저장해도 실제 봇이 그 체크아웃으로 옮겨 가지 않는다"""
    calls, loaded, agents = fake_launchd
    main, worktree = tmp_path / "main", tmp_path / "worktree"
    main.mkdir(), worktree.mkdir()
    _register(agents, main)
    loaded.add("com.askproject.bot")
    assert host.restart_from("bot", worktree) == ("other_repo", main)
    assert calls == []  # kickstart·bootout·bootstrap 모두 없음
    assert host.registered_repo("bot") == main


def test_restart_from_not_loaded(fake_launchd, tmp_path):
    calls, _, _ = fake_launchd
    assert host.restart_from("bot", tmp_path) == ("not_loaded", None)
    assert not any(c[0] == "kickstart" for c in calls)


class _S:
    def __init__(self, console_port=None, console_public_host=None):
        self.console_port, self.console_public_host = console_port, console_public_host


def test_console_port_preference():
    assert host.preferred_console_port(_S()) == 18787
    assert host.preferred_console_port(_S(console_public_host="ask.example.com")) == 8787  # 팀 공유를 이미 켠 설치
    assert host.preferred_console_port(_S(console_port=9000, console_public_host="ask.example.com")) == 9000
    assert host.preferred_console_port(_S(console_port=9000), cli_port=9100) == 9100


def test_console_port_moves_only_without_team_share():
    taken = {18787, 18788}
    free = lambda p: p not in taken  # noqa: E731
    assert host.choose_console_port(_S(), free=free)[0] == 18789  # 빈 포트로 옮긴다
    assert host.choose_console_port(_S(), free=lambda p: True) == (18787, None)
    port, note = host.choose_console_port(_S(console_port=18787, console_public_host="ask.example.com"), free=free)
    assert port == 18787 and "팀 공유" in note  # 대시보드 Service URL과 달라지면 끊기므로 옮기지 않는다


def test_port_free_sees_listener():
    import socket

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        assert not host.port_free(srv.getsockname()[1])


def test_restart_while_waiting_reregisters_instead_of_kickstart(fake_launchd, tmp_path):
    """시작하다 죽어 재시작 대기 중이면 kickstart가 멈추므로 다시 등록해 바로 띄운다"""
    calls, loaded, agents = fake_launchd
    repo = tmp_path / "main"
    repo.mkdir()
    _register(agents, repo)
    loaded.update({"com.askproject.bot", "waiting"})
    assert host.restart_from("bot", repo) == ("restarted", repo)
    assert not any(c[0] == "kickstart" for c in calls)
    assert [c[0] for c in calls if c[0] in ("bootout", "bootstrap")] == ["bootout", "bootstrap"]


def test_dev_tunnel_switch(fake_launchd, tmp_path):
    """개발 앱의 팀 공유 스위치: 켜면 plist를 쓰고(떠 있던 같은 레이블 터널은 먼저 내림), 앱을 끌 때는 내리기만,
    켤 때 다시 띄우고, 끄면 plist까지 지운다."""
    calls, loaded, agents = fake_launchd
    repo = tmp_path / "repo"
    repo.mkdir()
    label = "com.askproject.tunnel"
    loaded.add(label)  # 배포 앱이 띄운 터널
    host.tunnel_on(repo, tmp_path / "logs")
    d = plistlib.loads((agents / f"{label}.plist").read_bytes())
    assert d["ProgramArguments"][-1] == "tunnel" and d["WorkingDirectory"] == str(repo.resolve())
    assert [c[0] for c in calls if c[0] in ("bootout", "bootstrap")] == ["bootout", "bootstrap"]
    assert host.tunnel_registered()

    calls.clear()
    assert host.tunnel_pause() and ("bootout", f"{host._domain()}/{label}") in calls
    assert host.tunnel_registered()  # 켜 둔 설정은 남는다
    assert host.tunnel_resume() and calls[-1][0] == "bootstrap"
    assert not host.tunnel_resume()  # 이미 떠 있으면 건드리지 않는다

    host.tunnel_off()
    assert not host.tunnel_registered()
    assert not host.tunnel_resume() and not host.tunnel_pause()
