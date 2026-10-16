"""앱 모드 로그. launchd plist(번들 안, 정적)에는 사용자 경로를 적을 수 없으므로 프로세스가 직접 파일로 보낸다.

~/Library/Logs/AskProject/<작업>.log. 표준 출력·오류(fd 1·2)를 파일에 붙여 print·하위 프로세스 출력도 남긴다.
회전은 copytruncate 방식(사본을 .1로 밀고 원본을 비움)이라 열린 파일 서술자를 다시 열 필요가 없다.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

MAX_BYTES = 5_000_000
KEEP = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def rotate_if_needed(path: Path, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> bool:
    try:
        if path.stat().st_size < max_bytes:
            return False
    except FileNotFoundError:
        return False
    for i in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{i}")
        if older.exists():
            older.replace(path.with_name(f"{path.name}.{i + 1}"))
    shutil.copyfile(path, path.with_name(f"{path.name}.1"))
    with path.open("r+b") as f:
        f.truncate(0)
    return True


def setup(log_dir: Path | None, job: str | None) -> Path | None:
    """작업(job)으로 실행될 때만 파일로 보낸다. 앱이 부르는 일회성 CLI는 표준 출력 그대로(앱이 JSON을 읽는다)."""
    if not (log_dir and job):
        logging.basicConfig(level=logging.INFO, format=FORMAT)
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{job}.log"
    rotate_if_needed(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    sys.stdout = os.fdopen(1, "w", buffering=1, encoding="utf-8", closefd=False)
    sys.stderr = os.fdopen(2, "w", buffering=1, encoding="utf-8", closefd=False)
    logging.basicConfig(level=logging.INFO, format=FORMAT, stream=sys.stderr, force=True)
    sys.excepthook = lambda t, v, tb: logging.critical("처리되지 않은 예외", exc_info=(t, v, tb))
    return path
