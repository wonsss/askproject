"""AskProject CLI.

  askproject sync [--force] [--no-external]   레포 동기화 + 코드·구조·문서 인덱싱 + 노션·슬랙 수집
  askproject ask "질문" [--image 화면.png]     슬랙 없이 파이프라인 실행 (스크린샷은 메모리에서만 읽음)
  askproject eval [--ids q01,q02] [--limit N]  골든 세트 평가 → eval/results/
  askproject serve                            슬랙 봇 실행 (주기적 동기화 포함)
  askproject stats                            감사 로그 요약
  askproject console [--port 8787]            웹 콘솔 (로컬 전용, 읽기 전용, API 비용 없음)
  askproject host on|off|status|restart       맥북 호스트 모드 (봇·콘솔 상시 실행, 잠자기 방지, 매일 백업)
  askproject provider [api|subscription]      LLM 인증 경로 확인·전환 (subscription=로컬 claude CLI 구독)
  askproject power [default|lid-open|lid-closed]  맥북 절전 모드 확인·전환 (덮개 닫힘은 전원 연결 시만)
  askproject backup [--dest DIR]              감사 로그 백업 (+ 백업 사본 폴더·ASKPROJECT_BACKUP_UPLOAD_CMD로 맥 밖 복사)
  askproject restore-check [백업 파일]         백업 복구 확인 (무결성·질문 수)
  askproject backups [--json]                 백업 목록 (sha256 일치·질문 수)
  askproject restore <백업 파일> --yes         백업으로 DB 교체 (봇·콘솔을 끈 뒤, 지금 DB는 옆에 옮겨 둠)
  askproject maintenance [--vacuum]           오래된 커밋 스냅숏·WAL 정리
  askproject redact <query_id> --reason "…"   질문 텍스트 가림 (개인정보 유입 시)
  askproject doctor [--online]                환경 점검 (도구·설정·인덱스, --online이면 연결까지)
  askproject nightly                          매일 작업: 백업 → 복구 확인 → 정리 (launchd backup 작업)
  askproject paths --json                     실행 모드별 경로 (앱이 읽는다)
  askproject config get --json                설정 조회 (비밀값은 저장 여부만)
  askproject config set --json < patch.json   설정 변경 (검증·원자적 쓰기, 비밀값은 표준 입력으로만)
  askproject diagnostics [--out 파일.zip]       비밀값을 가린 진단 묶음 (지원 요청 첨부용)
  askproject setup manifest-url|pat-url|check <slack|github|claude>|repos|channels   첫 실행 마법사용 (JSON)
  askproject migrate                          DB 스키마·설정 형식 올리기 (백업 후, 다운그레이드 거부)
  askproject learn domains|glossary [--dry-run] [--json]   도메인 맵·용어 사전 AI 초안 + 검증 (적용은 따로)
  askproject learn domains|glossary --apply <제안 id|-> [--accept a,b]   승인한 항목만 적용 (-는 표준 입력 JSON)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from askproject import paths
from askproject.app import build_container

SYNC_INTERVAL_SEC = 600


def cmd_sync(force: bool, external: bool) -> None:
    c = build_container()
    r = c.refresh(external=external, force=force)
    print(f"{c.repo.name} @ {r.commit[:7]} (이전 {r.previous[:7] if r.previous else '-'}) · 변경 파일 {r.changed_files}개 · "
          f"{'재인덱싱' if r.reindexed else '변경 없음'}")
    if r.stats:
        print(json.dumps(r.stats, ensure_ascii=False, indent=2))


def _ensure_index(c) -> None:
    if c.store.file_count(c.repo.name) == 0:
        c.refresh()


def _read_image(path: str):
    from askproject.pipeline.screenshot import ALLOWED_TYPES, MAX_BYTES

    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".gif": "image/gif"}.get(Path(path).suffix.lower())
    data = Path(path).read_bytes()
    if media not in ALLOWED_TYPES or len(data) > MAX_BYTES:
        sys.exit("--image는 png·jpg·webp·gif, 5MB 이하만 됩니다.")
    return data, media


def cmd_ask(question: str, domains: str | None, image: str | None = None) -> None:
    from askproject.pipeline.screenshot import extract_screen
    from askproject.slack.blocks import CONFIDENCE_LABEL

    c = build_container()
    _ensure_index(c)
    forced = None if domains is None else [d for d in domains.split(",") if d]

    async def run():
        shot = None
        if image:
            data, media = _read_image(image)
            shot = await extract_screen(c.llm, data, media)  # 이미지는 저장하지 않고 문구·배치만 넘긴다
            print(f"📷 화면 문구 {len(shot.texts)}개 · 배치 {len(shot.layout)}줄 · 추출 ${shot.cost_usd:.4f}")
        return await c.service.answer(question, source="cli", forced_domains=forced, screenshot=shot)

    a = asyncio.run(run())
    if a.status != "answer":
        print(f"[{a.status}] {a.body}")
        for did, name in a.options:
            print(f"  - {did}: {name}   (askproject ask \"{question}\" --domains {did})")
        print(f"\n비용 ${a.cost_usd:.4f}")
        return
    scope = ",".join(a.scope.domains) if a.scope and a.scope.domains else "전체"
    print(f"[{CONFIDENCE_LABEL[a.confidence]}] main@{a.commit[:7]} · 범위 {scope} · 경로 {a.route} · "
          f"${a.cost_usd:.4f} · {a.duration_ms / 1000:.1f}s · 도구 {a.tool_calls}회\n")
    print(a.body)
    print("\n근거")
    for x in a.citations:
        loc = f"{x.path}/" if x.start == 0 else f"{x.path}:{x.start}-{x.end}"
        print(f"  ✅ [{x.source}] {loc} {x.note}")
    for x in a.unverified:
        loc = f"{x.path}/" if x.start == 0 else f"{x.path}:{x.start}-{x.end}"
        print(f"  ⚠️ {loc} ({x.reason})")
    if a.attachment:
        filename, content = a.attachment
        flow = a.attachment_kind == "flow"
        out = Path(c.settings.data_dir) / ("diagrams" if flow else "wireframes") / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        print(f"\n{'🧭 흐름도' if flow else '🖼 변경안 (원본 스크린샷과 비교)' if a.comparison else '🖼 와이어프레임'}: {out}")
    elif a.route == "wireframe":
        print("\n⚠️ 와이어프레임 HTML이 없습니다")
    if a.followups:
        print("\n이어서 물어보기")
        for q in a.followups:
            print(f"  - {q}")
    if a.config_dependent:
        print("\n⚙ 일부 값은 설정·환경값에 따라 달라질 수 있음")
    if a.error:
        print(f"\n오류: {a.error}", file=sys.stderr)


def cmd_eval(ids: str | None, limit: int | None, concurrency: int) -> None:
    from askproject.evaluation import load_golden, run_eval

    c = build_container()
    _ensure_index(c)
    golden_path = Path("eval/golden.yaml")
    if not golden_path.exists():
        raise SystemExit("eval/golden.yaml이 없습니다. eval/golden.example.yaml 형식으로 대상 레포의 문항을 작성하세요.")
    golden = load_golden(golden_path)
    if ids:
        wanted = set(ids.split(","))
        golden = [g for g in golden if g["id"] in wanted]
    if limit:
        golden = golden[:limit]
    commit = c.store.get_repo_commit(c.repo.name) or "unknown"
    _, summary, md = asyncio.run(run_eval(c.service, c.llm, golden, paths.eval_results_dir(), commit, concurrency))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n결과표: {md}")


def cmd_serve() -> None:
    from askproject.slack.app import build_app, serve

    c = build_container()
    s = c.settings
    if not (s.slack_bot_token and s.slack_app_token):
        sys.exit("ASKPROJECT_SLACK_BOT_TOKEN, ASKPROJECT_SLACK_APP_TOKEN이 필요합니다.")
    from askproject.host import acquire_serve_lock, hold_awake

    _lock = acquire_serve_lock(s.data_dir)  # noqa: F841 — 프로세스가 끝날 때까지 잡고 있어야 한다
    if _lock is None:
        sys.exit("봇이 이미 실행 중이에요(같은 질문에 답이 두 번 가지 않도록 하나만 띄웁니다). askproject host status로 확인하세요.")
    _awake = hold_awake(s.power_mode)  # noqa: F841 — 절전 모드(default면 None)
    from askproject.doctor import claude_preflight

    problem = claude_preflight(s)  # 키·크레딧 문제를 첫 질문이 아니라 시작할 때 알린다
    if problem:
        sys.exit(f"Claude를 호출할 수 없어 봇을 시작하지 않았어요: {problem}")
    c.refresh()

    async def periodic_sync() -> None:
        from askproject.logs import rotate_if_needed

        while True:
            await asyncio.sleep(SYNC_INTERVAL_SEC)
            rotate_if_needed(s.log_dir / "bot.log")
            try:
                await asyncio.to_thread(c.refresh)
            except Exception:
                logging.exception("periodic sync failed")

    async def main() -> None:
        from askproject.slack.heartbeat import Heartbeat

        app = build_app(s.slack_bot_token.get_secret_value(), c.service, c.store, s.escalation_channel_id(), c.llm,
                        allowed_channels=s.allowed_channel_ids())
        if not s.allowed_channel_ids():
            logging.warning("답할 채널 제한 없음(ASKPROJECT_ALLOWED_CHANNELS) — 봇이 초대된 모든 채널에서 답해요")
        hb_channel = s.host_channel or s.escalation_channel
        hb = Heartbeat(app.client, hb_channel, s.data_dir) if hb_channel else None
        if hb:
            await hb.wait_until_alone()  # 같은 슬랙 앱으로 다른 맥이 켜져 있으면 이벤트가 갈라지므로 기다린다
            asyncio.create_task(hb.run())
        else:
            logging.warning("호스트 중복 감지 꺼짐 — host_channel 또는 escalation_channel을 정하세요")
        asyncio.create_task(periodic_sync())
        try:
            await serve(app, s.slack_app_token.get_secret_value())
        finally:
            if hb:
                await hb.beat("stopped")

    asyncio.run(main())


def cmd_stats() -> None:
    c = build_container()
    row = c.store.execute(
        "SELECT COUNT(*) n, AVG(cost_usd) cost, SUM(cost_usd) total, AVG(duration_ms) ms, AVG(num_turns) turns, "
        "SUM(confidence='high') hi, SUM(confidence='medium') mid, SUM(confidence='low') lo FROM query_results"
    ).fetchone()
    routes = c.store.execute("SELECT route, COUNT(*) n, AVG(cost_usd) cost FROM query_results GROUP BY route").fetchall()
    fb = c.store.execute("SELECT value, COUNT(*) n FROM feedback GROUP BY value").fetchall()
    miss = c.store.execute("SELECT COUNT(*) n FROM tool_calls WHERE tool='widen_scope'").fetchone()["n"]
    print(f"질문 {row['n']}건 · 평균 ${row['cost'] or 0:.4f} · 합계 ${row['total'] or 0:.4f} · "
          f"{(row['ms'] or 0) / 1000:.1f}s · 대화 왕복 {row['turns'] or 0:.1f}회")
    print("경로별 " + ", ".join(f"{r['route']} {r['n']}건(평균 ${r['cost'] or 0:.4f})" for r in routes))
    print(f"확신도 높음 {row['hi'] or 0} / 보통 {row['mid'] or 0} / 낮음 {row['lo'] or 0} · 범위 확장(범위 미스) {miss}회")
    print("피드백 " + (", ".join(f"{r['value']} {r['n']}" for r in fb) or "없음"))


def cmd_console(port: int) -> None:
    """웹 콘솔. Claude API를 쓰지 않으므로 API 키 없이도 뜬다. 로컬(127.0.0.1)에서만 연다."""
    import uvicorn

    from askproject.config import load_repos
    from askproject.console.data import ConsoleData
    from askproject.console.server import create_app
    from askproject.pipeline.scoping import load_domain_map
    from askproject.settings import Settings

    s = Settings()
    repo = load_repos(s.config_dir)[0]
    names = {d.id: d.name for d in load_domain_map(s.config_dir).domains}
    import os
    import secrets

    data = ConsoleData(s.db_path, repo.name, repo.web_base, paths.eval_results_dir(), names)
    token = secrets.token_urlsafe(32)  # 실행마다 새로. 앱은 data/console.json에서 읽어 URL에 붙인다
    paths.atomic_write(s.data_dir / "console.json", json.dumps({"port": port, "token": token, "pid": os.getpid()}))
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    public, cf = None, None
    if s.console_public_host and s.cf_access_team_domain and s.cf_access_aud:
        from askproject.console.server import CloudflareAccess

        public, cf = s.console_public_host, CloudflareAccess(s.cf_access_team_domain, s.cf_access_aud)
        print(f"팀 공개: https://{public} (Cloudflare Access JWT 검증)")
    elif s.console_public_host:
        print("⚠️ ASKPROJECT_CONSOLE_PUBLIC_HOST만 있고 CF_ACCESS_TEAM_DOMAIN·CF_ACCESS_AUD가 없어 공개 접근을 막았어요")
    print(f"AskProject 콘솔: http://127.0.0.1:{port}/?key={token}  (로컬 전용, 읽기 전용, 종료는 Ctrl+C)")
    uvicorn.run(create_app(data, allowed_hosts=hosts, token=token, public_host=public, cf_access=cf),
                host="127.0.0.1", port=port, log_level="warning")


def cmd_backup(dest: str | None, keep_days: int | None) -> None:
    from askproject.ops import backup
    from askproject.settings import Settings

    s = Settings()
    r = backup(s.db_path, Path(dest) if dest else s.backup_dir, keep_days or s.backup_keep_days, s.backup_upload_cmd,
               s.backup_copy_dir, s.backup_copy_keep_days)
    if r.uploaded or r.copied:
        up = " · ".join(x for x in ("서버 밖 복사 완료" if r.uploaded else "",
                                     f"사본 폴더에 복사 {r.copied.parent}" if r.copied else "") if x)
    else:
        up = "맥 밖 복사 설정 없음(백업 사본 폴더 또는 ASKPROJECT_BACKUP_UPLOAD_CMD)"
    print(f"백업 {r.path} · {r.bytes / 1e6:.1f}MB · 질문 {r.queries}건 · sha256 {r.sha256[:12]}… · "
          f"오래된 백업 {len(r.pruned)}개 정리 · {up}")


def cmd_nightly() -> None:
    """launchd backup 작업: 백업 → 복구 확인 → 정리. 앞 단계가 실패하면 멈춘다(셸 없이 한 프로세스에서)."""
    cmd_backup(None, None)
    cmd_restore_check(None)
    cmd_maintenance(False)


def cmd_paths() -> None:
    from askproject.settings import Settings

    print(json.dumps(paths.describe(Settings()), ensure_ascii=False))


def cmd_config(action: str) -> None:
    from pydantic import ValidationError

    from askproject import config_store
    from askproject.settings import Settings

    s = Settings()
    if action == "get":
        print(json.dumps(config_store.get(s), ensure_ascii=False, default=str))
        return
    try:
        out = config_store.set_from_json(s, sys.stdin.read())
    except (ValidationError, ValueError) as exc:
        errors = exc.errors(include_url=False, include_input=False) if isinstance(exc, ValidationError) else None
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors) if errors else str(exc)
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps(out, ensure_ascii=False))


def cmd_migrate() -> None:
    from askproject.migrate import MigrationRefused, run
    from askproject.settings import Settings

    try:
        out = run(Settings())
    except MigrationRefused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(3)
    print(json.dumps(out, ensure_ascii=False))


def cmd_diagnostics(out: str | None) -> None:
    from askproject.diagnostics import build
    from askproject.settings import Settings

    path = build(Settings(), Path(out) if out else None)
    print(json.dumps({"path": str(path.resolve())}, ensure_ascii=False))


def cmd_backups(as_json: bool) -> None:
    import time

    from askproject.ops import list_backups
    from askproject.settings import Settings

    s = Settings()
    items = list_backups(s.backup_dir, s.backup_copy_dir)
    if as_json:
        print(json.dumps({"backup_dir": str(s.backup_dir.resolve()),
                          "copy_dir": str(s.backup_copy_dir) if s.backup_copy_dir else None, "backups": items},
                         ensure_ascii=False))
        return
    for b in items:
        sha = {True: "sha256 일치", False: "sha256 불일치", None: "sha256 기록 없음"}[b["sha256_ok"]]
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(b['mtime']))}  {b['kind']:11} {b['bytes'] / 1e6:6.1f}MB  "
              f"질문 {b['queries'] if b['queries'] is not None else '?'}건  {sha}  {b['path']}")
    if not items:
        print("백업이 없습니다. askproject backup을 먼저 실행하세요.")


def cmd_restore(path: str, yes: bool, as_json: bool) -> None:
    """백업으로 DB 교체. 봇·콘솔이 꺼져 있어야 한다(앱은 먼저 멈추고 부른다)."""
    from askproject.ops import RestoreRefused, restore
    from askproject.settings import Settings

    def fail(msg: str, code: int) -> None:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if as_json else f"❌ {msg}")
        sys.exit(code)

    if not yes:
        fail("복구는 지금 DB를 백업 시점으로 되돌려요(그 뒤 질문은 화면에서 사라지고, 지금 DB는 옆에 옮겨 둬요). "
             "확인했으면 --yes를 붙이세요.", 2)
    s = Settings()
    try:
        r = restore(s.db_path, Path(path), s.backup_dir)
    except RestoreRefused as exc:
        fail(str(exc), 3)
    except RuntimeError as exc:
        fail(str(exc), 1)
    if as_json:
        print(json.dumps(r, ensure_ascii=False))
        return
    print(f"✅ 복구했어요: {r['restored_from']} → {r['db']} · 질문 {r['queries']}건 · 마지막 질문 {r['latest']}")
    print(f"   옮겨 둔 이전 DB: {', '.join(r['moved_aside']) or '없음'}"
          + (f" · 복구 직전 백업 {r['pre_restore_backup']}" if r["pre_restore_backup"] else ""))
    if r["pre_restore_error"]:
        print(f"   ⚠️ 복구 직전 백업은 실패했어요({r['pre_restore_error']}). 옮겨 둔 파일이 원본이에요.")
    print("   봇을 켜고 askproject doctor로 확인하세요.")


def cmd_restore_check(path: str | None, as_json: bool = False) -> None:
    from askproject.ops import check_backup, latest_backup, verify_backup
    from askproject.settings import Settings

    target = Path(path) if path else latest_backup(Settings().backup_dir)
    if not target or not target.exists():
        if as_json:
            print(json.dumps({"ok": False, "error": "확인할 백업 파일이 없습니다."}, ensure_ascii=False))
            sys.exit(1)
        sys.exit("확인할 백업 파일이 없습니다. askproject backup을 먼저 실행하세요.")
    if as_json:
        try:
            r = check_backup(target)
        except Exception as exc:  # 백업이 아닌 파일·망가진 파일
            print(json.dumps({"ok": False, "path": str(target), "error": f"백업 파일로 열 수 없어요: {exc}"[:300]},
                             ensure_ascii=False))
            sys.exit(1)
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["ok"] else 1)
    r = verify_backup(target)
    ok = r["integrity"] == "ok"
    print(f"{'✅' if ok else '❌'} {target} · 무결성 {r['integrity']} · 스키마 v{r['schema_version']} · "
          f"질문 {r['queries']}건(결과 {r['results']}건) · 마지막 질문 {r['latest']}")
    if not ok:
        sys.exit(1)


def cmd_maintenance(vacuum: bool) -> None:
    import sqlite3

    from askproject.config import load_repos
    from askproject.ops import maintenance
    from askproject.settings import Settings
    from askproject.store.db import Store

    s = Settings()
    Store(s.db_path)  # 스키마·마이그레이션 적용
    before = s.db_path.stat().st_size
    conn = sqlite3.connect(s.db_path, isolation_level=None)
    try:
        r = maintenance(conn, load_repos(s.config_dir)[0].name, vacuum=vacuum)
    finally:
        conn.close()
    print(f"커밋 스냅숏 {r['snapshots_kept']}개 유지 · {r['snapshots_dropped']}개({r['rows_dropped']}행) 정리 · "
          f"WAL 정리 · DB {before / 1e6:.1f}MB → {s.db_path.stat().st_size / 1e6:.1f}MB"
          + (" · VACUUM" if r["vacuumed"] else ""))


def cmd_redact(query_id: str, reason: str, operator: str | None) -> None:
    import getpass

    from askproject.settings import Settings
    from askproject.store.db import Store

    who = operator or getpass.getuser()
    if not Store(Settings().db_path).redact(query_id, who, reason):
        sys.exit(f"질문을 찾을 수 없습니다: {query_id}")
    print(f"가림 완료: {query_id} (질문·답·도구 입력 텍스트). 행과 가림 기록은 남습니다. 처리자 {who}, 사유: {reason}")


def cmd_status(as_json: bool, check_claude: bool) -> None:
    """메뉴 막대 앱용 상태. 기본은 Claude를 부르지 않는다(--check-claude만 1토큰 호출)."""
    from askproject.app_status import collect
    from askproject.config import load_repos
    from askproject.settings import Settings

    s = Settings()
    try:
        repo = load_repos(s.config_dir)[0]
        name, web = repo.name, repo.web_base
    except (ValueError, OSError):  # 첫 실행: 레포를 아직 등록하지 않았다(마법사가 상태를 읽는다)
        name, web = "", ""
    st = collect(s, name, web, check_claude=check_claude)
    if as_json:
        print(json.dumps(st, ensure_ascii=False))
        return
    jobs = " · ".join(f"{k} {v['state']}" for k, v in st["jobs"].items())
    print(f"{jobs}\n오늘 질문 {st['today']['questions']}건 ${st['today']['cost']:.4f} · 이번 주 ${st['week']['cost']:.4f} · "
          f"확인 필요 {st['check']}건 · 최근 백업 {st['last_backup']['age_hours'] if st['last_backup'] else '-'}시간 전")


def cmd_provider(mode: str | None) -> None:
    """LLM 인증 경로 전환. .env에 기록하고, host 모드로 봇이 켜져 있으면 봇만 재시작해 바로 적용한다."""
    from askproject.settings import Settings, update_env_file

    s = Settings()
    if mode is None:
        print(f"현재 인증 경로: {s.llm_provider} (api=ANTHROPIC_API_KEY, subscription=로컬 claude CLI 구독)")
        return
    if mode == s.llm_provider:
        print(f"이미 {mode} 모드예요.")
        return
    if mode == "subscription" and paths.app_mode():
        # 슬랙 봇은 타인에게 답하므로 개인 구독 로그인을 쓰면 Anthropic 정책 위반이다. 개발 PoC에서만 허용
        sys.exit("앱에서는 구독 모드를 쓸 수 없어요(슬랙 봇은 다른 사람에게 답하므로 API 키가 필요해요).")
    update_env_file(s.env_path, "ASKPROJECT_LLM_PROVIDER", mode)
    warn = ""
    if mode == "subscription":
        from askproject.llm.claude_cli import cli_preflight

        problem = cli_preflight()
        if problem:
            warn = f"\n⚠️ {problem}"
    from askproject import host

    restarted = host.restart("bot") if sys.platform == "darwin" else False
    print(f".env에 ASKPROJECT_LLM_PROVIDER={mode} 저장"
          + (" · 봇을 재시작해 적용했어요" if restarted else " · 다음 serve 시작부터 적용돼요") + warn)


POWER_LABELS = {"default": "기본 — 맥이 잠들면 봇도 멈춤",
                "lid-open": "덮개 열림 — 깨어 있음(화면은 꺼져도 됨)",
                "lid-closed": "덮개 닫힘 — 전원 연결 중이면 닫아도 깨어 있음"}


def cmd_power(mode: str | None) -> None:
    """맥북 절전 모드 전환. .env에 기록하고 host 작업이 켜져 있으면 다시 등록해 적용한다."""
    from askproject import host
    from askproject.settings import Settings, update_env_file

    s = Settings()
    if mode is None:
        print(f"현재 절전 모드: {s.power_mode} ({POWER_LABELS[s.power_mode]})")
        return
    if mode == s.power_mode:
        print(f"이미 {mode} 모드예요.")
        return
    update_env_file(s.env_path, "ASKPROJECT_POWER_MODE", mode)
    # 잠자기 방지는 serve가 스스로 거므로 봇만 다시 띄우면 된다
    restarted = host.restart("bot") if sys.platform == "darwin" else False
    tail = " · 봇을 재시작해 적용했어요" if restarted else " · 다음 봇 시작부터 적용돼요"
    print(f"ASKPROJECT_POWER_MODE={mode} 저장{tail}")
    if mode == "lid-closed":
        print("⚠️ 전원 연결 중에만 유효해요. 배터리로는 덮개를 닫으면 잠들고, 가방에 넣으면 발열에 주의하세요.")
    elif mode == "default":
        print("⚠️ 맥이 잠들면 봇도 멈춥니다 — 상시 운영이면 lid-open 이상을 권해요.")


def cmd_host(action: str, port: int, job: str = "bot") -> None:
    """맥북 호스트 모드 (launchd). API 키 인증 그대로, Claude 비용은 질문을 받을 때만 든다."""
    from askproject import host
    from askproject.settings import Settings

    if sys.platform != "darwin":
        sys.exit("host 명령은 macOS 전용입니다. 서버는 docker compose를 쓰세요(docs/deploy-vultr.md).")
    s = Settings()
    logs = s.log_dir
    if action in ("on", "off") and paths.app_mode():
        sys.exit("앱 모드에서는 메뉴 막대 앱의 켜기·끄기를 쓰세요(SMAppService로 등록돼요).")
    if action == "on":
        names = host.on(Path.cwd(), logs, port)
        print(f"켰어요: {', '.join(names)} · 콘솔 http://127.0.0.1:{port} · 로그 {logs}/")
        print(f"절전 모드: {s.power_mode} ({POWER_LABELS[s.power_mode]}) — 바꾸려면 askproject power { '|'.join(host.POWER_MODES) }")
        if s.power_mode != "default":
            print("켜져 있는 동안 맥북은 잠자기에 들어가지 않아요(전원 연결 권장). 끌 때: askproject host off")
    elif action == "restart":
        if paths.app_mode():  # 앱 번들 작업은 번들 경로를 쓰므로 옮겨 갈 일이 없다
            ok = host.restart(job)
            print(f"{job} 재시작했어요" if ok else f"{job}이(가) 등록돼 있지 않아요")
            return
        state, registered = host.restart_from(job, Path.cwd())
        if state == "other_repo":
            sys.exit(f"{job}은(는) 다른 체크아웃({registered})에서 실행 중이라 재시작하지 않았어요. "
                     f"이 레포로 옮기려면 여기서 askproject host on을 직접 실행하세요.")
        if state == "failed":
            sys.exit(f"{job} 재시작 실패 — askproject host status로 확인하세요")
        print(f"{job} 재시작했어요" if state == "restarted" else f"{job}이(가) 등록돼 있지 않아요 — askproject host on으로 켜세요")
    elif action == "off":
        names = host.off()
        print(f"껐어요: {', '.join(names) or '실행 중인 것 없음'} · 잠자기 방지도 풀렸어요")
    else:
        for name, state, last in host.status(logs):
            print(f"{name:8} {state}")
            if last:
                print(f"         └ {last}")



def _learner(c):
    from askproject.learn import Learner, Workspace

    s = c.settings
    return Learner(c.store, c.repo, c.sync.path_for(c.repo), c.service.runner, c.service.code_tools,
                   Workspace(s.config_dir, s.data_dir, s.backup_dir), model=s.model_large,
                   structure_tools=c.service.structure_tools, doc_tools=c.service.doc_tools,
                   effort=s.agent_effort_large or None, provider=s.llm_provider)


ACTION_MARK = {"add": "+", "change": "~", "remove": "-", "locked": "🔒", "same": "="}


def _print_proposal(p: dict) -> None:
    print(f"제안 {p['id']} · {p['kind']} · 비용 ${p['cost_usd']:.3f} ({p['num_turns']}회, {p['model']})")
    for i in p["items"]:
        if i["action"] == "same":
            continue
        after = i["after"] if i["after"] is not None else i["before"]
        label = after.get("name", "") if isinstance(after, dict) else ", ".join(after or [])
        print(f"  {ACTION_MARK[i['action']]} {i['key']}  {label}" + (f"  ({i['locked_reason']})" if i["locked_reason"] else ""))
        for w in i["warnings"]:
            print(f"      ⚠ {w}")
    for r in p["rejected"]:
        print(f"  ✗ {r['key']}  버림: {r['reason']}")
    cov = p.get("coverage")
    if cov:
        print(f"  미분류 파일 비율 {cov['before']['uncovered_ratio']:.0%} → {cov['after']['uncovered_ratio']:.0%} (모두 적용 시)")
    print(f"적용: askproject learn {p['kind']} --apply {p['id']} [--accept 키1,키2]  (생략하면 + ~ 항목)")


def cmd_learn(kind: str, dry_run: bool, apply_ref: str | None, accept: str | None, as_json: bool, yes: bool,
              dismiss: str | None = None) -> None:
    from askproject.learn import LearnError, Workspace, apply

    def fail(msg: str) -> None:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if as_json else f"실패: {msg}")
        sys.exit(2)

    try:
        if dismiss:
            from askproject.settings import Settings

            s = Settings()
            Workspace(s.config_dir, s.data_dir, s.backup_dir).mark_proposal(dismiss, "dismissed_at")
            print(json.dumps({"ok": True}) if as_json else "버린 제안으로 표시했어요.")
            return
        if apply_ref:
            from askproject.settings import Settings

            s = Settings()
            keys = [k for k in accept.split(",") if k] if accept is not None else None
            if apply_ref == "-":
                body = json.loads(sys.stdin.read() or "{}")
                apply_ref, keys = body.get("proposal_id", ""), body.get("accept")
            out = apply(Workspace(s.config_dir, s.data_dir, s.backup_dir), apply_ref, keys)
            if out.get("kind") != kind:
                fail(f"이 제안은 {out.get('kind')} 제안이에요.")
            if as_json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                print(f"적용 {len(out['applied'])}개 · 건너뜀 {len(out['skipped'])}개 · 백업 {out['backup'] or '-'}")
                for sk in out["skipped"]:
                    print(f"  - {sk['key']}: {sk['reason']}")
                if out["restart_needed"]:
                    print("봇을 다시 시작하면 반영돼요.")
            return
        c = build_container()
        learner = _learner(c)
        plan = learner.plan(kind)
        if dry_run or not plan["ready"]:
            if as_json:
                print(json.dumps(plan, ensure_ascii=False))
            else:
                print(f"{kind} 학습 예상 비용 약 ${plan['estimate_usd']:.2f} (1회 상한 ${plan['max_usd']:.2f}, {plan['model']}) · "
                      f"재료 {plan['materials']}")
                if plan["reason"]:
                    print(plan["reason"])
                if plan["relearn_hint"]:
                    print(plan["relearn_hint"])
                if plan.get("pending"):
                    print(f"아직 적용하지 않은 제안: {plan['pending']['id']} (askproject learn {kind} --apply <id>)")
            if not plan["ready"] and not dry_run:
                sys.exit(2)
            return
        if not as_json:
            print(f"예상 비용 약 ${plan['estimate_usd']:.2f} (1회 상한 ${plan['max_usd']:.2f}, {plan['model']})")
            if not yes:
                if not sys.stdin.isatty() or input("계속할까요? [y/N] ").strip().lower() != "y":
                    sys.exit("취소했어요. (확인 없이 실행하려면 --yes)")
        proposal = asyncio.run(learner.propose(kind))
        print(json.dumps(proposal, ensure_ascii=False)) if as_json else _print_proposal(proposal)
    except LearnError as exc:
        fail(str(exc))
    except (ValueError, FileNotFoundError) as exc:
        fail(f"{type(exc).__name__}: {exc}")


def _prepare(cmd: str) -> None:
    """로그·폴더 준비. 앱 모드에서 launchd 작업(ASKPROJECT_JOB)으로 돌면 ~/Library/Logs로 보낸다."""
    import os

    from askproject import logs

    job = os.environ.get("ASKPROJECT_JOB")
    if not paths.app_mode():
        logs.setup(None, None)
        return
    from askproject.settings import Settings

    try:
        s = Settings()
    except ValueError as exc:  # 앱 모드 구독 설정 등 — 로그가 남도록 기본 경로로 띄우고 알린다
        logs.setup(paths.default_log_dir() if job else None, job)
        sys.exit(str(exc))
    logs.setup(s.log_dir if job else None, job)
    paths.ensure_app_dirs(s)
    if job:
        # launchd 작업: 앱이 migrate를 부르기 전에 재부팅 등으로 먼저 떠도 같은 잠금 아래 한 번만 올린다
        from askproject.migrate import MigrationRefused, run

        try:
            run(s)
        except MigrationRefused as exc:
            sys.exit(str(exc))


def main() -> None:
    from askproject import __protocol__, __version__

    parser = argparse.ArgumentParser(prog="askproject")
    parser.add_argument("--version", action="version", version=f"askproject {__version__} (protocol {__protocol__})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-external", action="store_true", help="노션·슬랙 수집 생략")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--domains", help="스코핑 없이 범위 지정 (쉼표 구분, 빈 값이면 전체)")
    ask.add_argument("--image", help="화면 스크린샷 경로 (변경 요청이면 변경안 목업)")
    ev = sub.add_parser("eval")
    ev.add_argument("--ids")
    ev.add_argument("--limit", type=int)
    ev.add_argument("--concurrency", type=int, default=2)
    sub.add_parser("serve")
    sub.add_parser("nightly", help="백업 → 복구 확인 → 정리 (매일 작업)")
    pa = sub.add_parser("paths", help="실행 모드별 경로 (앱용 JSON)")
    pa.add_argument("--json", action="store_true")
    cf = sub.add_parser("config", help="설정 조회·변경 (앱용 JSON, 변경은 표준 입력)")
    cf.add_argument("action", choices=["get", "set"])
    cf.add_argument("--json", action="store_true")
    ln = sub.add_parser("learn", help="도메인 맵·용어 사전 학습: AI 초안 → 검증 → 항목별 승인 → 적용")
    ln.add_argument("kind", choices=["domains", "glossary"])
    ln.add_argument("--dry-run", action="store_true", help="API 호출 없이 재료·예상 비용만")
    ln.add_argument("--apply", metavar="ID|-", help="제안 적용 (-면 표준 입력 {\"proposal_id\", \"accept\": [...]})")
    ln.add_argument("--accept", help="--apply ID와 함께: 적용할 항목 키(쉼표). 생략하면 추가·변경 항목")
    ln.add_argument("--yes", action="store_true", help="비용 확인 없이 실행")
    ln.add_argument("--dismiss", metavar="ID", help="제안을 버린 것으로 표시 (다시 불러오지 않음)")
    ln.add_argument("--json", action="store_true")
    sub.add_parser("migrate", help="DB 스키마·설정 형식 올리기 (백업 후, 다운그레이드 거부)")
    dg = sub.add_parser("diagnostics", help="비밀값을 가린 진단 묶음 zip")
    dg.add_argument("--out")
    sub.add_parser("stats")
    bk = sub.add_parser("backup", help="감사 로그 백업 (무결성 확인, 오래된 백업 정리, 서버 밖 복사)")
    bk.add_argument("--dest")
    bk.add_argument("--keep-days", type=int)
    rc = sub.add_parser("restore-check", help="백업 파일 복구 확인 (기본: 가장 최근 백업)")
    rc.add_argument("path", nargs="?")
    rc.add_argument("--json", action="store_true", help="앱용 JSON (sha256 일치·문제 목록 포함)")
    bl = sub.add_parser("backups", help="백업 목록 (매일·마이그레이션 직전·복구 직전·사본 폴더)")
    bl.add_argument("--json", action="store_true")
    rs = sub.add_parser("restore", help="백업으로 DB 교체 (봇·콘솔을 끈 뒤, 지금 DB는 옆에 옮겨 둠)")
    rs.add_argument("path")
    rs.add_argument("--yes", action="store_true", help="확인했음 (없으면 거부)")
    rs.add_argument("--json", action="store_true")
    mt = sub.add_parser("maintenance", help="오래된 커밋 스냅숏·WAL 정리 (감사 로그는 건드리지 않음)")
    mt.add_argument("--vacuum", action="store_true")
    rd = sub.add_parser("redact", help="질문 하나의 텍스트를 가림 (개인정보 유입 시, 행은 남김)")
    rd.add_argument("query_id")
    rd.add_argument("--reason", required=True)
    rd.add_argument("--operator")
    sta = sub.add_parser("status", help="봇·콘솔·비용·확인 필요·백업 상태 (메뉴 막대 앱용 --json)")
    sta.add_argument("--json", action="store_true")
    sta.add_argument("--check-claude", action="store_true", help="1토큰 호출로 키·크레딧 확인 (약 $0.00001)")
    ho = sub.add_parser("host", help="맥북 호스트 모드: 봇·콘솔 상시 실행, 잠자기 방지, 매일 백업 (macOS)")
    ho.add_argument("action", choices=["on", "off", "status", "restart"])
    ho.add_argument("job", nargs="?", default="bot", choices=["bot", "console", "backup"], help="restart 대상 (기본 bot)")
    ho.add_argument("--port", type=int, default=8787)
    pv = sub.add_parser("provider", help="LLM 인증 경로 확인·전환 (api=API 키, subscription=로컬 claude CLI 구독)")
    pv.add_argument("mode", nargs="?", choices=["api", "subscription"])
    pw = sub.add_parser("power", help="맥북 절전 모드 확인·전환 (default|lid-open|lid-closed)")
    pw.add_argument("mode", nargs="?", choices=["default", "lid-open", "lid-closed"])
    sub.add_parser("tunnel", help="웹 콘솔 팀 공유: Cloudflare 터널(대시보드 토큰) 실행 — launchd 작업용")
    con = sub.add_parser("console", help="웹 콘솔 (로컬 전용, 읽기 전용)")
    con.add_argument("--port", type=int, default=8787)
    dr = sub.add_parser("doctor")
    dr.add_argument("--online", action="store_true", help="슬랙·Claude·GitHub·노션 연결까지 확인")
    dr.add_argument("--json", action="store_true", help="앱용 JSON (실패 항목에 고칠 마법사 단계 포함)")
    su = sub.add_parser("setup", help="첫 실행 마법사용 점검·조회 (JSON)")
    su.add_argument("action", choices=["manifest-url", "pat-url", "check", "repos", "channels", "team-domain"])
    su.add_argument("target", nargs="?", choices=["slack", "github", "claude", "tunnel"])
    su.add_argument("--owner", help="pat-url: 레포 소유 조직(target_name)")
    su.add_argument("--host", help="team-domain: 팀 공유 공개 주소")
    args = parser.parse_args()
    _prepare(args.cmd)
    if args.cmd == "doctor":
        from askproject.doctor import run

        sys.exit(run(args.online, args.json))
    if args.cmd == "setup":
        from askproject.setup import run as setup_run

        print(json.dumps(setup_run(args.action, args.target, args.owner, args.host), ensure_ascii=False))
        return
    if args.cmd == "sync":
        cmd_sync(args.force, not args.no_external)
    elif args.cmd == "ask":
        cmd_ask(args.question, args.domains, args.image)
    elif args.cmd == "eval":
        cmd_eval(args.ids, args.limit, args.concurrency)
    elif args.cmd == "serve":
        cmd_serve()
    elif args.cmd == "backup":
        cmd_backup(args.dest, args.keep_days)
    elif args.cmd == "restore-check":
        cmd_restore_check(args.path, args.json)
    elif args.cmd == "backups":
        cmd_backups(args.json)
    elif args.cmd == "restore":
        cmd_restore(args.path, args.yes, args.json)
    elif args.cmd == "maintenance":
        cmd_maintenance(args.vacuum)
    elif args.cmd == "redact":
        cmd_redact(args.query_id, args.reason, args.operator)
    elif args.cmd == "status":
        cmd_status(args.json, args.check_claude)
    elif args.cmd == "host":
        cmd_host(args.action, args.port, args.job)
    elif args.cmd == "provider":
        cmd_provider(args.mode)
    elif args.cmd == "power":
        cmd_power(args.mode)
    elif args.cmd == "console":
        cmd_console(args.port)
    elif args.cmd == "tunnel":
        from askproject import tunnel
        from askproject.settings import Settings

        sys.exit(tunnel.run(Settings()))
    elif args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "nightly":
        cmd_nightly()
    elif args.cmd == "paths":
        cmd_paths()
    elif args.cmd == "config":
        cmd_config(args.action)
    elif args.cmd == "migrate":
        cmd_migrate()
    elif args.cmd == "learn":
        cmd_learn(args.kind, args.dry_run, args.apply, args.accept, args.json, args.yes, args.dismiss)
    elif args.cmd == "diagnostics":
        cmd_diagnostics(args.out)


if __name__ == "__main__":
    main()
