"""DB 운영: 백업·복구 확인·정리. 보존 정책은 '감사 로그 영구 보존'이다(docs/operations.md).

Claude API를 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

BACKUP_PREFIX = "askproject-"
BACKUP_SUFFIX = ".sqlite3"


@dataclass
class BackupResult:
    path: Path
    bytes: int
    sha256: str
    queries: int
    pruned: list[Path]
    uploaded: bool | None  # None = 업로드 설정 없음
    copied: Path | None = None  # 백업 사본 폴더(동기화 폴더)에 놓인 사본. None = 설정 없음


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_backup(path: Path) -> dict:
    """백업 파일을 읽기 전용으로 열어 무결성과 내용을 확인한다(복구 연습)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        n, last = conn.execute("SELECT COUNT(*), MAX(ts) FROM queries").fetchone()
        results = conn.execute("SELECT COUNT(*) FROM query_results").fetchone()[0]
    finally:
        conn.close()
    return {"integrity": integrity, "schema_version": version, "queries": n, "results": results,
            "latest": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "-"}


def _prune(folder: Path, keep_days: int, keep: Path) -> list[Path]:
    """folder의 askproject-*.sqlite3(와 .sha256) 중 keep_days 지난 것을 지운다. 다른 이름의 파일은 건드리지 않는다."""
    pruned = []
    cutoff = time.time() - keep_days * 86400
    for old in sorted(folder.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")):
        if old.name != keep.name and old.stat().st_mtime < cutoff:
            old.unlink()
            Path(str(old) + ".sha256").unlink(missing_ok=True)
            pruned.append(old)
    return pruned


def copy_to(out: Path, folder: Path, keep_days: int) -> Path:
    """백업과 .sha256를 동기화 폴더에 복사한다. 임시 이름으로 다 쓴 뒤 rename해서, 동기화 앱이 반쯤 쓴 파일을
    올리지 않게 한다(.sha256는 본 파일 다음에 놓는다 — sha256가 보이면 본 파일은 이미 완성돼 있다)."""
    if not folder.is_dir():
        raise RuntimeError(f"백업 사본 폴더가 없습니다(동기화 앱이 꺼졌거나 폴더를 옮겼나요?): {folder}")
    for src in (out, Path(str(out) + ".sha256")):
        tmp = folder / f".{src.name}.partial"
        try:
            shutil.copyfile(src, tmp)
            tmp.replace(folder / src.name)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"백업 사본 폴더에 복사하지 못했습니다: {exc}") from exc
    _prune(folder, keep_days, out)
    return folder / out.name


def backup(db_path: Path, dest: Path, keep_days: int = 14, upload_cmd: str | None = None,
           copy_dir: Path | None = None, copy_keep_days: int = 90) -> BackupResult:
    """SQLite 온라인 백업 → 무결성 확인 → sha256 기록 → 오래된 로컬 백업 정리 → (설정 시) 백업 사본 폴더에 복사
    → (설정 시) 서버 밖으로 복사.

    upload_cmd 예: "rclone copy {file} r2:askproject-backups" — {file} 자리에 백업 파일 경로가 들어간다.
    셸을 거치지 않고 실행한다(쉘 확장·파이프 없음).
    copy_dir: iCloud Drive·Google Drive 같은 동기화 폴더. 그 폴더의 오래된 사본은 copy_keep_days가 지나면 지운다.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}{BACKUP_SUFFIX}"
    tmp = out.with_suffix(".partial")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)  # WAL 포함 일관된 스냅숏, 봇이 쓰는 중에도 안전
        dst.execute("PRAGMA journal_mode=DELETE")  # 백업본은 -wal·-shm 없는 단일 파일로
    finally:
        dst.close()
        src.close()
    check = verify_backup(tmp)
    if check["integrity"] != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"백업 무결성 확인 실패: {check['integrity']}")
    os.chmod(tmp, 0o600)  # sqlite는 umask(보통 0644)로 만든다 — 감사 로그 사본은 본인만 읽게
    tmp.rename(out)
    digest = _sha256(out)
    out.with_suffix(out.suffix + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")

    pruned = _prune(dest, keep_days, out)
    copied = copy_to(out, copy_dir, copy_keep_days) if copy_dir else None

    uploaded = None
    if upload_cmd:
        args = [a.replace("{file}", str(out)) for a in shlex.split(upload_cmd)]
        if shutil.which(args[0]) is None:
            raise RuntimeError(f"업로드 명령을 찾을 수 없습니다: {args[0]}")
        r = subprocess.run(args, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"서버 밖 복사 실패({r.returncode}): {r.stderr.strip()[:300]}")
        uploaded = True
    return BackupResult(out, out.stat().st_size, digest, check["queries"], pruned, uploaded, copied)


def offsite(settings, latest: Path) -> bool:
    """가장 최근 백업이 맥 밖에도 있나: 업로드 명령이 설정됐거나, 백업 사본 폴더에 같은 이름의 사본이 있다."""
    if getattr(settings, "backup_upload_cmd", None):
        return True
    folder = getattr(settings, "backup_copy_dir", None)
    return bool(folder) and (Path(folder) / latest.name).is_file()


def latest_backup(dest: Path) -> Path | None:
    files = sorted(dest.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def maintenance(conn: sqlite3.Connection, repo: str, vacuum: bool = False) -> dict:
    """정리: 답변이 참조하지 않는 오래된 커밋 스냅숏 삭제, WAL 체크포인트, 통계 갱신, (선택) VACUUM.

    감사 로그(질문·답·근거·피드백)는 건드리지 않는다(영구 보존).
    """
    current = conn.execute("SELECT commit_sha FROM repo_state WHERE repo=?", (repo,)).fetchone()
    keep = {r[0] for r in conn.execute("SELECT DISTINCT commit_sha FROM queries WHERE commit_sha IS NOT NULL")}
    if current:
        keep.add(current[0])
    snapshots = {r[0] for r in conn.execute("SELECT DISTINCT commit_sha FROM commit_files WHERE repo=?", (repo,))}
    drop = sorted(snapshots - keep)
    removed = 0
    for sha in drop:
        removed += conn.execute("DELETE FROM commit_files WHERE repo=? AND commit_sha=?", (repo, sha)).rowcount
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA optimize")
    if vacuum:
        conn.execute("VACUUM")
    return {"snapshots_kept": len(snapshots) - len(drop), "snapshots_dropped": len(drop), "rows_dropped": removed,
            "vacuumed": vacuum}


def sha256_status(path: Path) -> bool | None:
    """옆의 .sha256 기록과 내용이 같은가. 기록이 없으면 None(다른 곳에서 가져온 파일 등)."""
    side = Path(str(path) + ".sha256")
    if not side.is_file():
        return None
    try:
        expected = side.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return False
    return expected == _sha256(path)


def _peek(path: Path) -> dict:
    """목록용 빠른 확인(무결성 검사는 하지 않는다 — 고른 뒤 restore-check에서)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            n, last = conn.execute("SELECT COUNT(*), MAX(ts) FROM queries").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"queries": None, "schema_version": None, "latest_ts": None, "error": str(exc)[:200]}
    return {"queries": n, "schema_version": version, "latest_ts": last, "error": None}


def list_backups(backup_dir: Path, copy_dir: Path | None = None) -> list[dict]:
    """복구 화면용 백업 목록(최신부터). 매일 백업 + 마이그레이션·복구 직전 백업 + 사본 폴더에만 있는 사본."""
    sources = [(backup_dir, "daily"), (backup_dir / "pre-migrate", "pre-migrate"),
               (backup_dir / "pre-restore", "pre-restore")]
    if copy_dir:
        sources.append((Path(copy_dir), "copy"))
    out, seen = [], set()
    for folder, kind in sources:
        if not folder.is_dir():
            continue
        for f in folder.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
            if kind == "copy" and f.name in seen:
                continue  # 같은 백업의 사본은 한 번만 보여 준다(로컬 것을 쓴다)
            if kind == "daily":
                seen.add(f.name)
            st = f.stat()
            out.append({"name": f.name, "path": str(f.resolve()), "kind": kind, "bytes": st.st_size,
                        "mtime": st.st_mtime, "sha256_ok": sha256_status(f), **_peek(f)})
    return sorted(out, key=lambda b: b["mtime"], reverse=True)


def check_backup(path: Path) -> dict:
    """복구 전 확인(restore-check --json): 무결성·스키마·질문 수 + sha256 일치."""
    from askproject.store.db import SCHEMA_VERSION

    r = verify_backup(path)
    sha_ok = sha256_status(path)
    problems = []
    if r["integrity"] != "ok":
        problems.append(f"무결성 확인 실패: {r['integrity']}")
    if sha_ok is False:
        problems.append("sha256가 기록과 달라요(파일이 바뀌었거나 덜 복사됐어요)")
    if r["schema_version"] > SCHEMA_VERSION:
        problems.append(f"더 새 앱이 만든 백업이에요(스키마 v{r['schema_version']} > v{SCHEMA_VERSION}). 최신 앱으로 복구하세요.")
    return {"ok": not problems, "path": str(path.resolve()), "sha256_ok": sha_ok, "problems": problems, **r}


class RestoreRefused(RuntimeError):
    pass


def _console_running(data_dir: Path) -> bool:
    import json
    import os

    try:
        info = json.loads((data_dir / "console.json").read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        return True
    except (OSError, ValueError, KeyError):
        return False


def restore(db_path: Path, backup_file: Path, backup_dir: Path) -> dict:
    """백업으로 DB 파일을 교체한다(docs/operations.md '복구 절차'). 행을 지우는 것이 아니라 파일을 바꾼다.

    1. 고른 백업 확인(무결성·sha256·스키마) — 문제가 있으면 아무것도 바꾸지 않는다
    2. 봇(serve.lock)·웹 콘솔이 돌고 있으면 거부한다. 잠금을 잡은 채로 진행해 도중에 봇이 뜨지 않게 한다
    3. 현재 DB를 pre-restore/에 한 번 더 온라인 백업(실패해도 4에서 원본을 그대로 옮겨 두므로 계속한다)
    4. 현재 DB와 -wal·-shm을 지우지 않고 옆으로 옮긴다: askproject.sqlite3.before-restore-<시각>(-wal, -shm)
    5. 백업을 임시 이름으로 복사 → rename으로 제자리에. 실패하면 4를 되돌린다
    """
    import fcntl

    from askproject.host import acquire_serve_lock

    backup_file = backup_file.expanduser().resolve()
    data_dir = db_path.parent
    if not backup_file.is_file():
        raise RestoreRefused(f"백업 파일이 없어요: {backup_file}")
    if db_path.exists() and backup_file.samefile(db_path):
        raise RestoreRefused("지금 쓰는 DB 파일은 복구 대상으로 고를 수 없어요.")
    check = check_backup(backup_file)
    if not check["ok"]:
        raise RestoreRefused("이 백업으로는 복구할 수 없어요: " + "; ".join(check["problems"]))

    lock = acquire_serve_lock(data_dir)
    if lock is None:
        raise RestoreRefused("봇이 실행 중이에요. 봇을 먼저 끄세요(앱의 '끄기' 또는 askproject host off).")
    mlock = (data_dir / "migrate.lock").open("w")
    try:
        try:
            fcntl.flock(mlock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RestoreRefused("마이그레이션이 진행 중이에요. 잠시 뒤 다시 시도하세요.") from None
        if _console_running(data_dir):
            raise RestoreRefused("웹 콘솔이 실행 중이에요. 봇·콘솔을 먼저 끄세요.")

        pre, pre_error = None, None
        if db_path.exists():
            try:
                pre = str(backup(db_path, backup_dir / "pre-restore", keep_days=365).path)
            except (sqlite3.Error, RuntimeError, OSError) as exc:  # 망가진 DB면 온라인 백업이 안 될 수 있다
                pre_error = str(exc)[:300]

        stamp = time.strftime("%Y%m%d-%H%M%S")
        aside = data_dir / f"{db_path.name}.before-restore-{stamp}"
        moved: list[tuple[Path, Path]] = []
        for suffix in ("", "-wal", "-shm"):  # 같은 접미사로 옮겨 두면 옮긴 파일 묶음도 SQLite로 그대로 열린다
            src = Path(str(db_path) + suffix)
            if src.exists():
                dst = Path(str(aside) + suffix)
                src.rename(dst)
                moved.append((src, dst))
        tmp = data_dir / f".{db_path.name}.restoring"
        try:
            shutil.copyfile(backup_file, tmp)
            tmp.replace(db_path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            for src, dst in reversed(moved):
                dst.rename(src)
            raise RuntimeError(f"백업을 제자리에 복사하지 못해 원래 DB로 되돌렸어요: {exc}") from exc
        after = verify_backup(db_path)
    finally:
        mlock.close()
        lock.close()
    return {"ok": True, "restored_from": str(backup_file), "db": str(db_path.resolve()),
            "moved_aside": [str(d) for _, d in moved], "pre_restore_backup": pre, "pre_restore_error": pre_error,
            "queries": after["queries"], "schema_version": after["schema_version"], "latest": after["latest"]}
