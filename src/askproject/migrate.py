"""`askproject migrate` — 서비스 시작 전 한 번에 하는 마이그레이션(앱 업데이트 직후·켜기 직전에 앱이 부른다).

1. 다운그레이드 거부: DB 스키마·설정 버전이 이 코드가 아는 것보다 새로우면 아무것도 바꾸지 않고 멈춘다.
2. 바꿀 것이 있으면 먼저 백업(SQLite 온라인 백업 API — WAL에만 있는 최근 쓰기까지 담긴다).
3. DB 스키마(store/db.py MIGRATIONS, 추가만) → 설정 파일(CONFIG_MIGRATIONS) 순서로 올린다.
봇·콘솔이 동시에 시작해도 한 프로세스만 하도록 data/migrate.lock을 잡는다.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

from askproject import paths
from askproject.config_store import CONFIG_VERSION
from askproject.store.db import SCHEMA_VERSION, SchemaTooNewError, Store, db_schema_version

META = "meta.json"
# 설정 파일 형식 변경: (도달 버전, 함수(config_dir)). 추가만 한다
CONFIG_MIGRATIONS: list[tuple[int, object]] = []


class MigrationRefused(RuntimeError):
    pass


def config_version(config_dir: Path) -> int:
    try:
        return int(json.loads((config_dir / META).read_text(encoding="utf-8"))["config_version"])
    except (OSError, ValueError, KeyError):
        return 0  # 기록 이전(v1 이전 형식과 같다)


def run(settings) -> dict:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / "migrate.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_locked(settings)


def _run_locked(settings) -> dict:
    db_from = db_schema_version(settings.db_path)
    cfg_from = config_version(settings.config_dir)
    if db_from is not None and db_from > SCHEMA_VERSION:
        raise MigrationRefused(f"DB 스키마 v{db_from}가 이 앱이 아는 v{SCHEMA_VERSION}보다 새로워요. "
                               "이전 버전 앱으로 되돌릴 수 없어요 — 최신 앱을 다시 설치하세요.")
    if cfg_from > CONFIG_VERSION:
        raise MigrationRefused(f"설정 형식 v{cfg_from}가 이 앱이 아는 v{CONFIG_VERSION}보다 새로워요. 최신 앱을 설치하세요.")

    backup_path = None
    db_needs = db_from is not None and db_from < SCHEMA_VERSION
    if db_needs:
        from askproject.ops import backup

        r = backup(settings.db_path, settings.backup_dir / "pre-migrate", keep_days=365)
        backup_path = str(r.path)
    try:
        Store(settings.db_path)  # 스키마 생성·마이그레이션(추가만)
    except SchemaTooNewError as exc:
        raise MigrationRefused(str(exc)) from exc
    for version, fn in CONFIG_MIGRATIONS:
        if version > cfg_from:
            fn(settings.config_dir)
    if cfg_from != CONFIG_VERSION and settings.config_dir.is_dir():
        paths.atomic_write(settings.config_dir / META, json.dumps({"config_version": CONFIG_VERSION}), mode=0o644)
    return {"ok": True, "schema": {"from": db_from, "to": SCHEMA_VERSION},
            "config": {"from": cfg_from, "to": CONFIG_VERSION}, "backup": backup_path}
