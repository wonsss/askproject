import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from askproject.ops import (RestoreRefused, backup, check_backup, latest_backup, list_backups, maintenance, offsite,
                            restore, verify_backup)
from askproject.store.db import REDACTED, SCHEMA_VERSION, QueryResult, Store


def _seed(st: Store, commit: str = "c1") -> str:
    qid = st.log_query("홍길동 010-1234-5678 푸시 시각?", "slack", "U1", "D1", "1.0", commit)
    st.log_result(qid, QueryResult(status="ok", answer="09:00이에요", confidence="high", route="agent",
                                   scope='{"domains": ["push-alerts"]}'))
    st.log_tool_call(qid, "code_search", {"query": "홍길동"}, "1건")
    st.log_citation(qid, "code", "a/b.ts", 1, 2, True, "ok")
    return qid


def test_backup_verify_prune_and_upload(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st)
    dest = tmp_path / "backups"
    dest.mkdir()
    old = dest / "askproject-20200101-000000.sqlite3"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    remote = tmp_path / "remote"
    remote.mkdir()
    r = backup(tmp_path / "db.sqlite3", dest, keep_days=14, upload_cmd=f"cp {{file}} {remote}")
    assert r.queries == 1 and r.uploaded and old in r.pruned and not old.exists()
    assert (remote / r.path.name).exists()  # 서버 밖 복사
    assert sorted(p.name for p in dest.iterdir()) == [r.path.name, r.path.name + ".sha256"]  # -wal·-shm 없음
    assert latest_backup(dest) == r.path
    v = verify_backup(r.path)
    assert v["integrity"] == "ok" and v["queries"] == 1 and v["schema_version"] == SCHEMA_VERSION


def test_backup_copies_to_sync_folder_and_prunes_only_own_files(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st)
    sync = tmp_path / "iCloud Drive" / "AskProject"  # 공백 있는 경로
    sync.mkdir(parents=True)
    stale = sync / "askproject-20200101-000000.sqlite3"
    stale.write_bytes(b"x")
    Path(str(stale) + ".sha256").write_text("x")
    other = sync / "notes.txt"  # 사용자 파일은 오래돼도 건드리지 않는다
    other.write_text("keep")
    for f in (stale, other):
        os.utime(f, (time.time() - 200 * 86400,) * 2)
    r = backup(tmp_path / "db.sqlite3", tmp_path / "b", copy_dir=sync, copy_keep_days=90)
    assert r.copied == sync / r.path.name and r.uploaded is None
    assert (sync / r.path.name).read_bytes() == r.path.read_bytes()
    assert (sync / (r.path.name + ".sha256")).read_text().split()[0] == r.sha256
    assert not stale.exists() and other.exists()
    assert not list(sync.glob(".*partial"))  # 임시 파일이 남지 않는다
    s = SimpleNamespace(backup_upload_cmd=None, backup_copy_dir=sync)
    assert offsite(s, r.path)
    (sync / r.path.name).unlink()
    assert not offsite(s, r.path)  # 사본이 실제로 없으면 맥 밖 사본으로 치지 않는다
    assert offsite(SimpleNamespace(backup_upload_cmd="rclone copy {file} r:x", backup_copy_dir=None), r.path)


def test_backup_copy_to_missing_folder_fails_after_local_backup(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st)
    with pytest.raises(RuntimeError, match="백업 사본 폴더가 없습니다"):
        backup(tmp_path / "db.sqlite3", tmp_path / "b", copy_dir=tmp_path / "unmounted")
    assert latest_backup(tmp_path / "b")  # 로컬 백업은 이미 남아 있다


def test_backup_upload_failure_is_reported(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st)
    with pytest.raises(RuntimeError, match="찾을 수 없습니다"):
        backup(tmp_path / "db.sqlite3", tmp_path / "b", upload_cmd="no-such-uploader {file}")
    with pytest.raises(RuntimeError, match="서버 밖 복사 실패"):
        backup(tmp_path / "db.sqlite3", tmp_path / "b", upload_cmd="cp {file} /nonexistent/dir/")


def test_maintenance_drops_only_unreferenced_snapshots(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st, commit="c1")
    st.set_repo_state("demo-repo", "c3")
    for sha in ("c1", "c2", "c3"):
        st.record_commit_files("demo-repo", sha, [("a/b.ts", f"blob-{sha}")])
    conn = sqlite3.connect(tmp_path / "db.sqlite3", isolation_level=None)
    r = maintenance(conn, "demo-repo")
    left = {x[0] for x in conn.execute("SELECT DISTINCT commit_sha FROM commit_files")}
    assert left == {"c1", "c3"} and r["snapshots_dropped"] == 1  # 답변이 쓰는 c1, 현재 c3는 남김
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1  # 감사 로그는 그대로


def test_redact_hides_text_keeps_rows_and_trigger(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    qid = _seed(st)
    assert st.redact(qid, "marco", "개인정보 유입")
    q = st.execute("SELECT question FROM queries WHERE id=?", (qid,)).fetchone()[0]
    a = st.execute("SELECT answer, scope FROM query_results WHERE query_id=?", (qid,)).fetchone()
    t = st.execute("SELECT input_json FROM tool_calls WHERE query_id=?", (qid,)).fetchone()[0]
    assert q == REDACTED and a[0] == REDACTED and a[1] is None and t == "{}"
    assert st.execute("SELECT COUNT(*) FROM citations WHERE query_id=?", (qid,)).fetchone()[0] == 1  # 행은 남김
    rec = st.execute("SELECT operator, reason FROM redactions WHERE query_id=?", (qid,)).fetchone()
    assert tuple(rec) == ("marco", "개인정보 유입")
    with pytest.raises(sqlite3.IntegrityError):  # 트리거가 다시 걸려 있다
        st.execute("UPDATE queries SET question='x' WHERE id=?", (qid,))
    with pytest.raises(sqlite3.IntegrityError):  # 가림 기록도 지울 수 없다
        st.execute("DELETE FROM redactions")
    assert not st.redact("nope", "marco", "x")
    with pytest.raises(ValueError):
        st.redact(qid, "marco", " ")


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "db.sqlite3"
    Store(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE redactions")
    conn.execute("PRAGMA user_version = 0")  # 버전 관리 이전에 만든 DB
    conn.commit()
    conn.close()
    st = Store(path)
    assert st.schema_version == SCHEMA_VERSION
    assert st.execute("SELECT name FROM sqlite_master WHERE name='redactions'").fetchone()


def test_list_backups_reports_sha_and_counts(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    _seed(st)
    dest, sync = tmp_path / "b", tmp_path / "sync"
    sync.mkdir()
    r = backup(tmp_path / "db.sqlite3", dest, copy_dir=sync)
    pre = backup(tmp_path / "db.sqlite3", dest / "pre-migrate", keep_days=365)
    only_copy = sync / "askproject-20200101-000000.sqlite3"  # 이 맥엔 없고 사본 폴더에만 있는 백업(새 맥)
    only_copy.write_bytes(r.path.read_bytes())
    tampered = dest / "askproject-20200102-000000.sqlite3"
    tampered.write_bytes(r.path.read_bytes())
    Path(str(tampered) + ".sha256").write_text("0" * 64 + "  x\n")
    items = list_backups(dest, sync)
    by = {(b["kind"], b["name"]): b for b in items}
    assert ("copy", r.path.name) not in by  # 로컬에도 있는 사본은 한 번만
    assert by[("daily", r.path.name)]["sha256_ok"] is True and by[("daily", r.path.name)]["queries"] == 1
    assert by[("pre-migrate", pre.path.name)]["sha256_ok"] is True
    assert by[("copy", only_copy.name)]["sha256_ok"] is None
    assert by[("daily", tampered.name)]["sha256_ok"] is False
    assert not check_backup(tampered)["ok"] and check_backup(r.path)["ok"]


def _restore_env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    st = Store(data / "askproject.sqlite3")
    _seed(st)
    old = backup(data / "askproject.sqlite3", tmp_path / "backups")  # 질문 1건 시점
    _seed(st)  # 백업 뒤 질문 1건 더(복구하면 화면에서 사라지지만 옮겨 둔 파일엔 남는다)
    return data, st, old


def test_restore_moves_current_db_aside_and_puts_backup_in_place(tmp_path):
    data, st, old = _restore_env(tmp_path)
    db = data / "askproject.sqlite3"
    assert Path(str(db) + "-wal").exists()  # WAL 모드에서 쓰는 중
    r = restore(db, old.path, tmp_path / "backups")
    assert r["ok"] and r["queries"] == 1
    aside = [Path(p) for p in r["moved_aside"]]
    assert aside and aside[0].name.startswith("askproject.sqlite3.before-restore-")
    conn = sqlite3.connect(f"file:{aside[0]}?mode=ro", uri=True)  # 옮겨 둔 묶음은 그대로 열린다(-wal 포함)
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 2
    conn.close()
    assert verify_backup(Path(r["pre_restore_backup"]))["queries"] == 2  # 복구 직전 백업
    assert Path(r["pre_restore_backup"]).parent.name == "pre-restore"
    assert verify_backup(db)["queries"] == 1
    assert not list(data.glob(".*restoring"))


def test_restore_refuses_while_bot_runs_or_backup_is_bad(tmp_path):
    from askproject.host import acquire_serve_lock

    data, _, old = _restore_env(tmp_path)
    db = data / "askproject.sqlite3"
    before = db.read_bytes()
    lock = acquire_serve_lock(data)  # 봇이 실행 중
    with pytest.raises(RestoreRefused, match="봇이 실행 중"):
        restore(db, old.path, tmp_path / "backups")
    lock.close()
    (data / "console.json").write_text(json.dumps({"pid": os.getpid(), "port": 8787, "token": "t"}))
    with pytest.raises(RestoreRefused, match="웹 콘솔"):
        restore(db, old.path, tmp_path / "backups")
    (data / "console.json").unlink()
    Path(str(old.path) + ".sha256").write_text("0" * 64 + "  x\n")
    with pytest.raises(RestoreRefused, match="sha256"):
        restore(db, old.path, tmp_path / "backups")
    with pytest.raises(RestoreRefused, match="없어요"):
        restore(db, tmp_path / "nope.sqlite3", tmp_path / "backups")
    assert db.read_bytes() == before and not list(data.glob("*.before-restore-*"))  # 아무것도 바꾸지 않았다
