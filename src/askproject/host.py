"""맥북 호스트 모드: launchd로 봇·웹 콘솔을 띄우고(죽으면 다시 뜸), 켜져 있는 동안 잠자기를 막고,
매일 새벽 DB 백업을 돌린다.

- 앱 모드: 작업은 앱 번들의 Contents/Library/LaunchAgents plist를 SMAppService로 등록한다(앱이 켜고 끔).
  여기서는 상태 조회·재시작(kickstart)만 한다. 레이블이 같아서 job_states는 두 모드에서 그대로 쓴다.
- 개발 모드: 아래 `host on`이 ~/Library/LaunchAgents에 uv run을 부르는 plist를 직접 만든다. LLM 인증은 .env의 ASKPROJECT_LLM_PROVIDER가 정한다
(api=ANTHROPIC_API_KEY, subscription=로컬 claude CLI 구독 로그인 — 로컬 PoC 전용).

  askproject host on      등록하고 켠다
  askproject host off     끄고 등록을 지운다
  askproject host status  실행 여부·콘솔 주소·최근 로그
"""

from __future__ import annotations

import fcntl
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LABEL_PREFIX = "com.askproject"
# 8787은 wrangler dev 등 개발 서버와 자주 겹쳐 덜 흔한 번호를 기본으로 쓴다
CONSOLE_PORT = 18787
# 이 변경 전에 팀 공유를 켠 설치는 Cloudflare 대시보드 Service URL이 8787을 가리킨다(포트를 따로 정하지 않았을 때)
LEGACY_CONSOLE_PORT = 8787
PORT_FALLBACK_TRIES = 20
POWER_MODES = ("default", "lid-open", "lid-closed")


def _caffeinate(mode: str) -> list[str]:
    """절전 모드 → caffeinate 인자. lid-closed(-s)는 전원 연결 중에만 유효하다(배터리로는 잠듦)."""
    return {"lid-open": ["/usr/bin/caffeinate", "-i"],
            "lid-closed": ["/usr/bin/caffeinate", "-s"]}.get(mode, [])


def hold_awake(mode: str) -> subprocess.Popen | None:
    """serve가 사는 동안 잠자기를 막는다(caffeinate -w <pid>: 봇이 죽으면 같이 풀린다).
    프로세스 안에서 거므로 절전 모드를 바꿀 때 plist를 다시 만들 필요 없이 봇만 재시작하면 된다."""
    args = _caffeinate(mode)
    if not args or sys.platform != "darwin":
        return None
    return subprocess.Popen([*args, "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def power_info(mode: str) -> dict:
    """절전 모드·자동 로그인·FileVault 상태 (메뉴 막대 앱이 status --json으로 읽는다)."""
    def _loginwindow(key: str) -> str | None:
        r = subprocess.run(["defaults", "read", "/Library/Preferences/com.apple.loginwindow", key],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    fv = subprocess.run(["fdesetup", "status"], capture_output=True, text=True)
    return {
        "mode": mode,
        "autologin_user": _loginwindow("autoLoginUser"),
        "filevault_on": "is On" in fv.stdout,
    }


def acquire_serve_lock(data_dir: Path):
    """봇 중복 실행 방지. 잠금을 얻으면 파일 객체(프로세스가 끝날 때 풀림), 이미 실행 중이면 None."""
    data_dir.mkdir(parents=True, exist_ok=True)
    f = (data_dir / "serve.lock").open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f


@dataclass(frozen=True)
class Job:
    name: str          # bot | console | backup
    label: str
    args: list[str]
    keep_alive: bool
    calendar: dict | None = None  # 매일 실행 시각


def share_configured(settings) -> bool:
    """팀 공유 주소를 정했는지. 정했으면 대시보드가 고정 포트를 가리키므로 콘솔 포트를 옮기지 않는다."""
    return bool(getattr(settings, "console_public_host", None))


def preferred_console_port(settings, cli_port: int | None = None) -> int:
    """--port > ASKPROJECT_CONSOLE_PORT > (팀 공유를 이미 켠 설치면 8787) > 기본 18787"""
    if cli_port:
        return cli_port
    if getattr(settings, "console_port", None):
        return settings.console_port
    return LEGACY_CONSOLE_PORT if share_configured(settings) else CONSOLE_PORT


def port_free(port: int, host_: str = "127.0.0.1") -> bool:
    """묶을 수 있고, 이미 받고 있는 프로그램도 없어야 빈 포트다(IPv6 *:포트로 받는 프로그램은 IPv4 bind만으론 안 보일 수 있다)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex((host_, port)) == 0:
            return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host_, port))
        return True
    except OSError:
        return False


def choose_console_port(settings, cli_port: int | None = None, free=port_free) -> tuple[int, str | None]:
    """(쓸 포트, 알릴 말). 팀 공유가 꺼져 있으면 사용 중인 포트를 피해 다음 빈 포트로 옮긴다.
    팀 공유를 켰으면 옮기지 않는다 — 대시보드 Service URL과 달라지면 공유 주소가 끊긴다."""
    port = preferred_console_port(settings, cli_port)
    if free(port):
        return port, None
    if share_configured(settings):
        return port, (f"{port} 포트를 다른 프로그램이 쓰고 있어요. 팀 공유를 켜 둬서 포트를 옮기지 않아요"
                      f"(Cloudflare 대시보드 Service URL이 http://127.0.0.1:{port}). 그 프로그램을 끄세요.")
    for p in range(port + 1, port + PORT_FALLBACK_TRIES + 1):
        if free(p):
            return p, f"{port} 포트를 다른 프로그램이 쓰고 있어 {p}로 열었어요."
    return port, f"{port}~{port + PORT_FALLBACK_TRIES} 포트가 모두 사용 중이에요."


def jobs(repo: Path, uv: str, port: int | None = None) -> list[Job]:
    run = [uv, "run", "--project", str(repo), "askproject"]
    # 잠자기 방지는 serve가 스스로 건다(hold_awake). 절전 모드는 .env의 ASKPROJECT_POWER_MODE
    return [
        Job("bot", f"{LABEL_PREFIX}.bot", [*run, "serve"], keep_alive=True),
        # 포트를 박지 않으면 콘솔이 실행할 때마다 정한다(설정 포트 → 사용 중이면 빈 포트, choose_console_port)
        Job("console", f"{LABEL_PREFIX}.console", [*run, "console", *(["--port", str(port)] if port else [])],
            keep_alive=True),
        Job("backup", f"{LABEL_PREFIX}.backup", [*run, "nightly"], keep_alive=False, calendar={"Hour": 3, "Minute": 30}),
    ]


def plist_for(job: Job, repo: Path, log_dir: Path) -> dict:
    d = {
        "Label": job.label,
        "ProgramArguments": job.args,
        "WorkingDirectory": str(repo),  # .env를 여기서 읽는다
        "RunAtLoad": job.keep_alive,
        "StandardOutPath": str(log_dir / f"{job.name}.log"),
        "StandardErrorPath": str(log_dir / f"{job.name}.log"),
        # 셸 환경을 물려받지 않는다: 셸에 남은 ANTHROPIC_* 값이 .env의 봇 키를 덮지 않게
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
    }
    if job.keep_alive:
        d["KeepAlive"] = True
        d["ThrottleInterval"] = 60  # 키·크레딧 문제로 바로 죽을 때 1분에 한 번만 다시 시도
    if job.calendar:
        d["StartCalendarInterval"] = job.calendar
    return d


def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _loaded(label: str) -> bool:
    return _launchctl("print", f"{_domain()}/{label}").returncode == 0


def on(repo: Path, log_dir: Path, port: int | None = None) -> list[str]:
    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    log_dir.mkdir(parents=True, exist_ok=True)
    _agents_dir().mkdir(parents=True, exist_ok=True)
    done = []
    for job in jobs(repo.resolve(), uv, port):
        path = _agents_dir() / f"{job.label}.plist"
        path.write_bytes(plistlib.dumps(plist_for(job, repo.resolve(), log_dir.resolve())))
        if _loaded(job.label):
            _launchctl("bootout", f"{_domain()}/{job.label}")
        r = _launchctl("bootstrap", _domain(), str(path))
        if r.returncode != 0:
            raise RuntimeError(f"{job.label} 등록 실패: {r.stderr.strip()}")
        done.append(job.name)
    return done


def off() -> list[str]:
    done = []
    for name in ("bot", "console", "backup"):
        label = f"{LABEL_PREFIX}.{name}"
        if _loaded(label):
            _launchctl("bootout", f"{_domain()}/{label}")
            done.append(name)
        (_agents_dir() / f"{label}.plist").unlink(missing_ok=True)
    return done


def restart(name: str) -> bool:
    """등록된 작업 하나를 다시 띄운다(kickstart -k). 등록돼 있지 않으면 False."""
    label = f"{LABEL_PREFIX}.{name}"
    r = _launchctl("print", f"{_domain()}/{label}")
    if r.returncode != 0:
        return False
    plist = _agents_dir() / f"{label}.plist"
    if "\tpid = " not in r.stdout and plist.is_file():
        # 시작하다 죽어 재시작 대기(ThrottleInterval) 중이면 kickstart는 바로 띄우지 못하고 멈춘다.
        # 다시 등록하면 대기가 풀려 바로 뜬다(설정을 고친 직후 1분씩 기다리지 않게)
        _launchctl("bootout", f"{_domain()}/{label}")
        return _launchctl("bootstrap", _domain(), str(plist)).returncode == 0
    return _launchctl("kickstart", "-k", f"{_domain()}/{label}").returncode == 0


def registered_repo(name: str) -> Path | None:
    """개발 모드로 등록된 plist의 WorkingDirectory(작업이 코드·.env·data를 읽는 레포). plist가 없으면 None."""
    p = _agents_dir() / f"{LABEL_PREFIX}.{name}.plist"
    try:
        wd = plistlib.loads(p.read_bytes()).get("WorkingDirectory")
    except Exception:
        return None
    return Path(wd) if wd else None


def restart_from(name: str, repo: Path) -> tuple[str, Path | None]:
    """설정을 바꾼 개발 앱이 부르는 재시작. `on`처럼 plist를 다시 쓰지 않고 등록된 작업만 kickstart한다.
    등록된 plist가 다른 체크아웃을 가리키면 건드리지 않는다 — 실행 중인 봇이 부른 쪽(예: worktree)의
    코드·.env·data로 옮겨 가 감사 로그가 다른 DB에 쌓이지 않게.
    반환: (restarted | not_loaded | other_repo | failed, 등록된 레포)"""
    registered = registered_repo(name)
    if registered is not None and registered.resolve() != repo.resolve():
        return "other_repo", registered
    if not _loaded(f"{LABEL_PREFIX}.{name}"):
        return "not_loaded", registered
    return ("restarted" if restart(name) else "failed"), registered


def job_states(log_dir: Path) -> dict[str, dict]:
    """작업별 상태. state: running | waiting(재시작 대기·예약) | off"""
    out = {}
    for name in ("bot", "console", "backup", "tunnel"):  # tunnel: 팀 공유를 켰을 때만 등록된다
        label = f"{LABEL_PREFIX}.{name}"
        r = _launchctl("print", f"{_domain()}/{label}")
        pid, last_exit = None, None
        if r.returncode != 0:
            state = "off"
        else:
            info = dict(line.strip().split(" = ", 1) for line in r.stdout.splitlines() if " = " in line)
            pid = int(info["pid"]) if info.get("pid", "").isdigit() else None
            last_exit = info.get("last exit code")
            state = "running" if pid else "waiting"
        log = log_dir / f"{name}.log"
        last = ""
        if log.exists():
            lines = [x for x in log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                     if x.strip() and " INFO httpx" not in x]
            last = lines[-1][:200] if lines else ""
        out[name] = {"state": state, "pid": pid, "last_exit": last_exit, "last_log": last}
    return out


def status(log_dir: Path) -> list[tuple[str, str, str]]:
    """(이름, 상태 문장, 최근 로그 한 줄) — CLI 표시용"""
    out = []
    for name, j in job_states(log_dir).items():
        if j["state"] == "off":
            text = "꺼짐"
        elif j["state"] == "running":
            text = f"실행 중 (pid {j['pid']})"
        else:
            text = "대기 (매일 03:30)" if name == "backup" else f"재시작 대기 · 마지막 종료 {j['last_exit'] or '-'}"
        out.append((name, text, j["last_log"]))
    return out
