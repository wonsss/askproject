"""진단 묶음(askproject diagnostics): 지원 요청에 첨부할 zip. 비밀값은 넣지 않고, 로그·출력에 섞인 토큰도 가린다.

담는 것: 버전·경로, 설정 요약(비밀값은 저장 여부만), doctor 결과(오프라인), 상태 JSON, 로그 꼬리.
레포 이름·채널 ID 같은 회사 설정은 들어가므로, 받는 사람을 확인하고 보내라고 안내한다.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from askproject import paths

SECRET_RE = re.compile(
    r"(xox[abposr]-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+|sk-ant-[A-Za-z0-9_-]+|github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+"
    r"|ntn_[A-Za-z0-9]+|secret_[A-Za-z0-9]+|eyJ[A-Za-z0-9+/=_.-]{20,}|(?<=key=)[A-Za-z0-9_-]{20,}|(?<=Bearer )[A-Za-z0-9._-]+"
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"  # URL에 박힌 user:pass (업로드 명령 오류 등)
    r"|(?<=\"console_key\": \")[^\"]+)")
TAIL_LINES = 400


def redact(text: str) -> str:
    return SECRET_RE.sub("[가림]", text)


def _scrub_status(text: str) -> str:
    """상태 JSON에서 콘솔 실행 토큰과 최근 질문 원문을 뺀다(질문에는 개인정보·사내 내용이 있을 수 있다)."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if data.get("console_key"):
        data["console_key"] = "[가림]"
    for r in data.get("recent") or []:
        if isinstance(r, dict) and "question" in r:
            r["question"] = "[가림]"
    return json.dumps(data, ensure_ascii=False, indent=2)


def _tail(path: Path, n: int = TAIL_LINES) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    return "\n".join(lines) + "\n"


def build(settings, out: Path | None = None) -> Path:
    from askproject import config_store
    from askproject.doctor import offline_checks

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out or settings.data_dir / "diagnostics" / f"askproject-diagnostics-{stamp}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    files["paths.json"] = json.dumps(paths.describe(settings), ensure_ascii=False, indent=2)
    b = paths.bundle_dir()
    if b and (b / "BUILD_INFO.json").is_file():
        files["build_info.json"] = (b / "BUILD_INFO.json").read_text()
    try:
        files["config.json"] = json.dumps(config_store.get(settings), ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        files["config.json"] = f"설정 읽기 실패: {exc}"
    try:
        checks = offline_checks(settings)
        files["doctor.txt"] = "\n".join(f"{c.status} {c.name}: {c.detail}" for c in checks) + "\n"
    except Exception as exc:
        files["doctor.txt"] = f"doctor 실패: {exc}\n"
    try:
        from askproject.cli import cmd_status

        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_status(True, False)
        files["status.json"] = _scrub_status(buf.getvalue())
    except (Exception, SystemExit) as exc:
        files["status.json"] = f"상태 읽기 실패: {exc}"
    for log in sorted(settings.log_dir.glob("*.log")):
        files[f"logs/{log.name}"] = _tail(log)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, redact(text))
    out.chmod(0o600)
    return out
