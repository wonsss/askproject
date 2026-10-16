"""도메인 맵·용어 사전 학습: AI 초안 → 결정적 검증 → 미리 보기 → 항목별 승인 → 적용.

- 초안: 에이전트(AgentRunner 포트)가 읽기 전용 도구(list_dir·code_search·find_screen 등)로 레포를 훑고 JSON을 낸다.
  재료는 sync가 만든 인덱스(폴더 구조·구조 맵·문서 제목)와 감사 로그의 최근 질문이다. 쓰기·셸·외부 전송 도구는 없다.
- 검증(LLM 아님): paths 패턴이 인덱스 파일에 하나 이상 걸리는지(0이면 버림), 용어 식별자가 코드에 있는지(rg),
  어느 도메인에도 안 걸리는 파일 비율, 기존 항목과의 차이(추가·변경·삭제).
- 적용: 승인된 항목만 병합해 원자적으로 쓴다. 쓰기 직전 기존 파일을 백업하고 머리말 주석을 유지한다.
  제안을 만든 뒤 파일이 바뀌었으면 적용하지 않는다(그사이 손으로 고친 내용을 덮지 않게).
- 잠금: 학습이 쓴 항목은 지문을 data/learn/learned.json에 남긴다. 지문이 없거나 달라진 항목(사람이 쓰거나 고친 항목)과
  `locked: true` 도메인은 잠금 — 다음 학습은 바꾸지 않고 제안으로만 보여 준다. `locked: false`를 쓰면 풀린다.
- 레포가 바뀌어도 자동으로 다시 학습하지 않는다(비용). 새 폴더가 생기면 `relearn_hint`로 알려만 준다.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from askproject import paths
from askproject.agent.base import AgentRequest, AgentResponse, AgentRunner, ToolSpec
from askproject.config import RepoConfig
from askproject.indexing.filters import PathFilter, matches
from askproject.llm.pricing import estimate_cost
from askproject.store.db import REDACTED, Store
from askproject.tools.code_tools import CodeTools, ToolSession
from askproject.tools.doc_tools import DocTools
from askproject.tools.structure_tools import StructureTools

KINDS = ("domains", "glossary")
FILES = {"domains": "domains.yaml", "glossary": "glossary.yaml"}
BUDGET_USD = 2.0  # 실제 학습 1회 상한(api 모드). 질문 1건 상한(agent_max_budget_usd)과 따로 둔다
MAX_TURNS = {"domains": 20, "glossary": 16}
TYPICAL_TURNS = {"domains": 12, "glossary": 10}
FINAL_OUTPUT_TOKENS = {"domains": 3500, "glossary": 3000}
FIXED_PROMPT_TOKENS = 3000   # 시스템 프롬프트 + 도구 정의 + CLI 기본 머리말
TURN_GROWTH_TOKENS = 1800    # 대화 한 번에 늘어나는 맥락(도구 결과)
TURN_OUTPUT_TOKENS = 350
CHARS_PER_TOKEN = 3          # 경로·영문 위주 재료. 한국어가 많으면 더 든다(보수적으로 잡음)
KEEP_PROPOSALS = 20
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9_$./:-]{2,60}$")
CATCH_ALL = {"**", "**/*", "*", "*/**", "**/**"}
WIDE_SHARE = 0.5


class LearnError(Exception):
    """사용자에게 그대로 보여 줄 실패 사유."""


# ---------- 파일·지문 ----------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def header_comments(text: str) -> str:
    """파일 맨 앞의 주석·빈 줄 묶음. 다시 쓸 때 그대로 앞에 붙인다."""
    out = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            out.append(line)
        else:
            break
    return ("\n".join(out).rstrip() + "\n") if any(l.strip() for l in out) else ""


def _domain_core(d: dict) -> dict:
    return {"id": d.get("id"), "name": d.get("name") or "", "summary": d.get("summary") or "",
            "terms": list(d.get("terms") or []), "paths": list(d.get("paths") or [])}


def fingerprint(kind: str, item: Any) -> str:
    core = _domain_core(item) if kind == "domains" else list(item or [])
    return hashlib.sha1(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def lock_reason(kind: str, key: str, item: Any, learned: dict) -> str | None:
    """잠긴 항목이면 이유. 학습이 쓰고 그대로인 항목만 학습이 바꿀 수 있다."""
    if kind == "domains" and isinstance(item, dict):
        if item.get("locked") is True:
            return "locked: true로 잠근 항목"
        if item.get("locked") is False:
            return None
    fp = (learned.get(kind) or {}).get(key)
    if fp is None:
        return "직접 쓴 항목"
    if fp != fingerprint(kind, item):
        return "손으로 고친 항목"
    return None


@dataclass
class Workspace:
    """학습이 읽고 쓰는 위치. 앱 모드면 ~/Library 아래(paths)."""

    config_dir: Path
    data_dir: Path
    backup_dir: Path

    @property
    def learn_dir(self) -> Path:
        return self.data_dir / "learn"

    def file(self, kind: str) -> Path:
        return self.config_dir / FILES[kind]

    def load_learned(self) -> dict:
        p = self.learn_dir / "learned.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            return {}

    def save_learned(self, learned: dict) -> None:
        paths.atomic_write(self.learn_dir / "learned.json", json.dumps(learned, ensure_ascii=False, indent=1))

    def save_proposal(self, proposal: dict) -> None:
        d = self.learn_dir / "proposals"
        paths.atomic_write(d / f"{proposal['id']}.json", json.dumps(proposal, ensure_ascii=False, indent=1))
        for old in sorted(d.glob("*.json"))[:-KEEP_PROPOSALS]:
            old.unlink(missing_ok=True)

    def load_proposal(self, pid: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", pid or ""):
            raise LearnError("제안 id 형식이 아니에요.")
        p = self.learn_dir / "proposals" / f"{pid}.json"
        if not p.is_file():
            raise LearnError("제안을 찾지 못했어요. 다시 학습해 주세요.")
        return json.loads(p.read_text(encoding="utf-8"))


def current_items(kind: str, text: str) -> tuple[dict, dict[str, Any]]:
    """(파일 전체 raw, key → 항목). domains는 id, glossary는 용어가 key다."""
    raw = yaml.safe_load(text) if text.strip() else None
    raw = raw if isinstance(raw, dict) else {}
    if kind == "domains":
        items = {str(d["id"]): d for d in raw.get("domains") or [] if isinstance(d, dict) and d.get("id")}
    else:
        items = {str(k): list(v) if isinstance(v, (list, tuple)) else [v] for k, v in raw.items() if v is not None}
    return raw, items


# ---------- 재료 ----------

def _dir_counts(files: list[str], depth: int = 3, min_files: int = 2) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, min(depth, len(parts)) + 1):
            key = "/".join(parts[:i]) + "/"
            counts[key] = counts.get(key, 0) + 1
    return {k: v for k, v in counts.items() if v >= min_files}


def _section(title: str, lines: list[str], max_chars: int) -> str:
    out, used = [], 0
    for line in lines:
        if used + len(line) > max_chars:
            out.append(f"… 외 {len(lines) - len(out)}줄 (도구로 더 보세요)")
            break
        out.append(line)
        used += len(line) + 1
    return f"## {title}\n" + ("\n".join(out) if out else "(없음)")


def build_materials(store: Store, repo: str, kind: str, current_text: str, domains_text: str = "") -> tuple[str, dict]:
    """에이전트에게 줄 재료(결정적). (프롬프트 본문, 숫자 요약)."""
    files = [r["path"] for r in store.execute("SELECT path FROM files WHERE repo=? ORDER BY path", (repo,))]
    dirs = _dir_counts(files)
    struct = {k: store.execute("SELECT key, path FROM structure WHERE repo=? AND kind=? ORDER BY key", (repo, k)).fetchall()
              for k in ("route", "manifest", "api")}
    docs = store.execute("SELECT source, path, title FROM doc_files ORDER BY source, path").fetchall()
    questions = [r["question"] for r in store.execute(
        "SELECT question FROM queries WHERE source != 'eval' AND question != ? ORDER BY ts DESC LIMIT 40", (REDACTED,))]
    parts = [
        _section("폴더 구조 (폴더/ 파일 수)", [f"{k} {v}" for k, v in sorted(dirs.items())], 9000),
        _section("화면 (라우트·앱 manifest)", [f"{r['key']} — {r['path']}" for r in [*struct["route"], *struct["manifest"]]], 6000),
        _section("API", [f"{r['key']} — {r['path']}" for r in struct["api"]], 6000),
        _section("문서 (출처 경로 「제목」)", [f"{r['source']} {r['path']} 「{r['title']}」" for r in docs], 5000),
        _section("최근 기획자 질문", [q.replace("\n", " ")[:120] for q in questions], 4000),
        _section(f"지금 {FILES[kind]}", current_text.splitlines(), 4000),
    ]
    if kind == "glossary" and domains_text:
        parts.append(_section("지금 domains.yaml (용어 참고)", domains_text.splitlines(), 3000))
    stats = {"files": len(files), "routes": len(struct["route"]) + len(struct["manifest"]), "apis": len(struct["api"]),
             "docs": len(docs), "questions": len(questions)}
    return "\n\n".join(parts), stats


def estimate_usd(model: str, prompt_chars: int, kind: str, turns: int) -> float:
    """토큰 × 단가(llm/pricing). 첫 대화에 프롬프트를 캐시에 쓰고, 이후 대화는 앞 맥락을 캐시로 읽는다고 본다."""
    p = FIXED_PROMPT_TOKENS + prompt_chars // CHARS_PER_TOKEN
    read = write = 0
    for i in range(turns):
        if i == 0:
            write += p
        else:
            read += p + (i - 1) * TURN_GROWTH_TOKENS
            write += TURN_GROWTH_TOKENS
    out = turns * TURN_OUTPUT_TOKENS + FINAL_OUTPUT_TOKENS[kind]
    return round(estimate_cost(model, 0, out, read, write) or 0.0, 3)


# ---------- 에이전트 ----------

SCHEMA = {
    "domains": """{"domains": [{"id": "orders", "name": "주문", "summary": "주문 생성·목록·상세, 취소·환불",
  "terms": ["주문", "결제", "취소", "환불"],
  "paths": ["apps/api/src/orders/**", "apps/web/app/orders/**", "docs/**/*order*"],
  "evidence": ["화면 /orders — apps/web/app/orders/page.tsx", "API POST /orders", "문서 docs/orders.md"]}]}""",
    "glossary": """{"glossary": [{"term": "주문", "identifiers": ["order", "Order", "orders"],
  "evidence": ["apps/api/src/orders/order.service.ts"]}]}""",
}

SYSTEM = {
    "domains": """\
당신은 코드베이스를 읽고 기획자용 '도메인 맵' 초안을 쓰는 분석가입니다. 도구는 모두 읽기 전용입니다.
도메인 맵은 기획자가 질문할 때 쓰는 기능 단위(예: 주문, 알림, 로그인)이고, paths는 그 도메인 질문에서 검색·읽기를
허용할 경로(하드 필터)입니다. paths가 빠지면 그 도메인 질문에서 해당 코드를 아예 못 보니 넓게, 틀리지 않게 잡습니다.

규칙:
- 도메인은 5~15개. 화면·API·문서를 근거로 기획자가 부르는 한국어 이름(name)을 붙입니다.
- id는 영문 소문자·숫자·-·_ (예: orders). 지금 파일에 있는 도메인은 같은 id를 씁니다.
- paths는 레포 루트 기준 gitignore 스타일 글롭(**, *, {a,b})입니다. 화면(웹·앱), API(컨트롤러·서비스·모델),
  공용 타입, 관련 문서를 함께 담습니다. '**'처럼 레포 전체를 여는 패턴은 쓰지 않습니다.
- 경로는 재료의 폴더 구조나 list_dir·code_search로 실제로 있는 것만 씁니다(검증에서 0건이면 버려집니다).
- terms는 기획자가 질문에 쓸 한국어 낱말 3~8개, evidence는 이 도메인을 정한 근거 2~4개(화면 라우트·API·문서 경로).
- 도구는 꼭 필요한 만큼만(10회 안팎) 씁니다. 마지막 메시지에는 아래 형식의 JSON만 ```json 블록으로 냅니다.
{schema}
""",
    "glossary": """\
당신은 코드베이스를 읽고 '용어 사전' 초안을 쓰는 분석가입니다. 도구는 모두 읽기 전용입니다.
용어 사전은 기획자가 쓰는 한국어 용어 → 코드에서 그 개념을 가리키는 영문 식별자(클래스·함수·변수·테이블·경로 일부)
힌트입니다. 봇이 한국어 질문을 코드 검색어로 바꿀 때 씁니다.

규칙:
- 용어는 30~80개. 화면 문구·문서·최근 질문에 나오는 기획 용어를 고릅니다. 코드 식별자와 이름이 달라 찾기 어려운 용어를 우선합니다.
- identifiers는 용어마다 1~5개, 코드에 실제로 있는 그대로의 철자(대소문자 포함)만 씁니다. code_search로 확인합니다
  (검증에서 코드에 없는 식별자는 버려집니다). 'id'·'data'처럼 너무 흔한 낱말은 쓰지 않습니다.
- evidence는 식별자를 찾은 파일 경로 1~2개.
- 지금 파일에 있는 용어는 그대로 둘지 고칠지 판단해 같은 용어로 다시 냅니다.
- 도구는 꼭 필요한 만큼만(10회 안팎) 씁니다. 마지막 메시지에는 아래 형식의 JSON만 ```json 블록으로 냅니다.
{schema}
""",
}


def learn_tools(session: ToolSession, code: CodeTools, structure: StructureTools | None,
                docs: DocTools | None) -> list[ToolSpec]:
    """학습용 읽기 전용 도구. 질문 답변과 달리 감사 로그(tool_calls)에 남기지 않는다."""
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    s = {"type": "string"}

    def h(fn):
        async def handler(args: dict) -> str:
            return fn(args)
        return handler

    tools = [
        ToolSpec("list_dir", "디렉터리 바로 아래 항목과 파일 수를 보여준다.", obj({"prefix": s}),
                 h(lambda a: code.list_dir(session, a.get("prefix", "")))),
        ToolSpec("code_search", "레포에서 문자열을 검색해 '파일:줄: 내용'을 최대 20건 돌려준다. path_glob으로 좁힐 수 있다.",
                 obj({"query": s, "path_glob": s}, ["query"]),
                 h(lambda a: code.search(session, a["query"], a.get("path_glob")))),
        ToolSpec("read_code", "파일의 지정 구간을 줄 번호와 함께 읽는다(최대 200줄).",
                 obj({"path": s, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
                 h(lambda a: code.read(session, a["path"], int(a.get("start", 1)), a.get("end")))),
    ]
    if structure:
        tools += [
            ToolSpec("find_screen", "화면을 URL·라우트·키워드로 찾아 화면 파일과 직접 호출하는 API를 보여준다.",
                     obj({"query": s}, ["query"]), h(lambda a: structure.find_screen(session, a["query"]))),
            ToolSpec("find_api", "API 경로 일부·클라이언트 함수명·컨트롤러명으로 클라이언트 → 서버 연결을 찾는다.",
                     obj({"query": s}, ["query"]), h(lambda a: structure.find_api(session, a["query"]))),
        ]
    if docs:
        tools += [
            ToolSpec("search_docs", "레포 문서·노션·슬랙 수집본을 검색한다.", obj({"query": s}, ["query"]),
                     h(lambda a: docs.search(session, a["query"]))),
            ToolSpec("read_doc", "문서의 지정 구간을 읽는다(최대 120줄).",
                     obj({"path": s, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
                     h(lambda a: docs.read(session, a["path"], int(a.get("start", 1)), a.get("end")))),
        ]
    return tools


def parse_draft(kind: str, text: str) -> list[dict]:
    """에이전트 출력에서 초안 항목 목록. glossary는 {"용어": [...]} 형태도 받는다."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    body = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise LearnError(f"AI 초안을 읽지 못했어요(JSON 형식 아님): {exc}") from exc
    raw = data.get(kind) if isinstance(data, dict) else None
    if kind == "glossary" and isinstance(raw, dict):
        raw = [{"term": k, "identifiers": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        raise LearnError(f"AI 초안에 '{kind}' 목록이 없어요.")
    return [x for x in raw if isinstance(x, dict)]


# ---------- 검증 ----------

def _strs(v: Any, limit: int, width: int = 200) -> list[str]:
    if isinstance(v, str):
        v = [v]
    out = [str(x).strip()[:width] for x in (v or []) if str(x).strip()] if isinstance(v, (list, tuple)) else []
    return list(dict.fromkeys(out))[:limit]


def _pattern_problem(p: str) -> str | None:
    if p in CATCH_ALL:
        return "레포 전체를 여는 패턴"
    if p.startswith("/") or ".." in p.split("/"):
        return "레포 밖 경로"
    return None


def make_rg_counter(repo_path: Path, repo: RepoConfig):
    """식별자 → 들어 있는 파일 수(rg, 제외 규칙 적용). rg가 없으면 None을 돌려준다(확인 못 함)."""
    flt = PathFilter(repo.exclude, repo.include_exceptions)

    def count(ident: str) -> int | None:
        rg = paths.rg_path()
        if not rg:
            return None
        cmd = [rg, "-l", "--fixed-strings", "--case-sensitive", "--color", "never"]
        for pattern in (*repo.exclude, "**/.env", "**/.env.*"):
            cmd += ["-g", f"!{pattern}"]
        cmd += ["-e", ident, "."]
        try:
            proc = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode not in (0, 1):
            return None
        return sum(1 for line in proc.stdout.splitlines() if flt.allowed(line.removeprefix("./")))

    return count


def _coverage(files: list[str], domains: list[dict], shared: list[str]) -> dict:
    globs = tuple(p for d in domains for p in d.get("paths") or [])
    covered = [f for f in files if globs and matches(f, globs)]
    uncovered = [f for f in files if not (globs and matches(f, globs)) and not (shared and matches(f, tuple(shared)))]
    top = sorted(_dir_counts(uncovered, depth=2, min_files=1).items(), key=lambda kv: (-kv[1], kv[0]))
    # 2단계 폴더를 우선 보여 준다(1단계 폴더는 2단계 합과 겹친다)
    top = [kv for kv in top if kv[0].count("/") == 2][:5] or top[:5]
    total = len(files) or 1
    return {"files": len(files), "covered": len(covered), "uncovered": len(uncovered),
            "uncovered_ratio": round(len(uncovered) / total, 3),
            "top_uncovered": [{"dir": k, "files": v} for k, v in top]}


def validate(kind: str, draft: list[dict], files: list[str], current: dict[str, Any], learned: dict,
             count_ident=None, shared: list[str] | None = None) -> dict:
    """초안 → 미리 보기(items·rejected·coverage·summary). LLM을 쓰지 않는다."""
    items: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    total = len(files) or 1
    proposed: dict[str, dict] = {}

    for raw in draft:
        warnings: list[str] = []
        if kind == "domains":
            key = str(raw.get("id") or "").strip().lower()
            name = str(raw.get("name") or "").strip()[:40]
            if not ID_RE.match(key):
                rejected.append({"key": key or "(id 없음)", "reason": "id 형식이 아니에요(영문 소문자·숫자·-·_)"})
                continue
            if not name:
                rejected.append({"key": key, "reason": "이름(name)이 없어요"})
                continue
            kept, matched = [], set()
            for p in _strs(raw.get("paths"), 20, 200):
                p = p.removeprefix("./")
                problem = _pattern_problem(p)
                hits = [] if problem else [f for f in files if matches(f, (p,))]
                if problem or not hits:
                    warnings.append(f"경로 제외: {p} ({problem or '걸리는 파일 0개'})")
                    continue
                kept.append(p)
                matched.update(hits)
            if not kept:
                rejected.append({"key": key, "reason": "실제 파일에 걸리는 경로가 하나도 없어요", "warnings": warnings})
                continue
            if len(matched) / total > WIDE_SHARE:
                warnings.append(f"레포 파일의 {round(100 * len(matched) / total)}%가 이 도메인에 걸려요 — 범위가 너무 넓을 수 있어요")
            after = {"id": key, "name": name, "summary": str(raw.get("summary") or "").strip()[:200],
                     "terms": _strs(raw.get("terms"), 12, 30), "paths": kept}
            extra = {"file_count": len(matched)}
        else:
            key = str(raw.get("term") or "").strip()[:30]
            if not key:
                rejected.append({"key": "(용어 없음)", "reason": "용어가 비었어요"})
                continue
            kept = []
            for ident in _strs(raw.get("identifiers"), 8, 60):
                if not IDENT_RE.match(ident):
                    warnings.append(f"식별자 제외: {ident} (식별자 형식 아님)")
                    continue
                n = count_ident(ident) if count_ident else None
                if n == 0:
                    warnings.append(f"식별자 제외: {ident} (코드에 없음)")
                    continue
                if n is None:
                    warnings.append(f"{ident}: 코드에 있는지 확인하지 못했어요(rg 없음)")
                kept.append(ident)
            if not kept:
                rejected.append({"key": key, "reason": "코드에서 찾은 식별자가 없어요", "warnings": warnings})
                continue
            after, extra = kept, {}
        if key in seen:
            continue
        seen.add(key)
        proposed[key] = after
        evidence = _strs(raw.get("evidence"), 6)
        before = current.get(key)
        if before is None:
            action, reason = "add", None
        else:
            reason = lock_reason(kind, key, before, learned)
            same = (fingerprint(kind, before) == fingerprint(kind, after))
            if same:
                action = "same"
            else:
                action = "locked" if reason else "change"
        items.append({"key": key, "action": action, "before": _plain(kind, before), "after": after,
                      "evidence": evidence, "warnings": warnings, "locked_reason": reason if action == "locked" else None,
                      "default_accept": action in ("add", "change"), **extra})

    for key, before in current.items():
        if key in proposed:
            continue
        reason = lock_reason(kind, key, before, learned)
        if reason:
            continue  # 잠긴 항목은 초안에 없어도 그대로 둔다
        items.append({"key": key, "action": "remove", "before": _plain(kind, before), "after": None, "evidence": [],
                      "warnings": ["이번 초안에 없어요 — 지울지 직접 판단하세요"], "locked_reason": None,
                      "default_accept": False})

    summary = {a: sum(1 for i in items if i["action"] == a) for a in ("add", "change", "remove", "locked", "same")}
    summary["rejected"] = len(rejected)
    out = {"items": items, "rejected": rejected, "summary": summary}
    if kind == "domains":
        cur = [d for d in current.values() if isinstance(d, dict)]
        locked_or_kept = [d for k, d in current.items() if k not in proposed or lock_reason(kind, k, d, learned)]
        result = [*locked_or_kept, *(proposed[i["key"]] for i in items if i["action"] in ("add", "change", "same"))]
        out["coverage"] = {"before": _coverage(files, cur, shared or []),
                           "after": _coverage(files, result, shared or [])}
    return out


def _plain(kind: str, item: Any) -> Any:
    if item is None:
        return None
    return _domain_core(item) | ({"locked": item["locked"]} if "locked" in item else {}) if kind == "domains" else list(item)


# ---------- 실행 ----------

@dataclass
class Learner:
    store: Store
    repo: RepoConfig
    repo_path: Path
    runner: AgentRunner
    code_tools: CodeTools
    workspace: Workspace
    model: str
    structure_tools: StructureTools | None = None
    doc_tools: DocTools | None = None
    effort: str | None = None
    provider: str = "api"

    def _files(self) -> list[str]:
        return [r["path"] for r in self.store.execute("SELECT path FROM files WHERE repo=? ORDER BY path", (self.repo.name,))]

    def _inputs(self, kind: str) -> tuple[str, str, dict]:
        text = _read(self.workspace.file(kind))
        domains_text = _read(self.workspace.file("domains")) if kind == "glossary" else ""
        body, stats = build_materials(self.store, self.repo.name, kind, text, domains_text)
        return text, body, stats

    def plan(self, kind: str) -> dict:
        """--dry-run: API 호출 없이 재료 크기·예상 비용·다시 학습 힌트."""
        text, body, stats = self._inputs(kind)
        prompt_chars = len(SYSTEM[kind]) + len(SCHEMA[kind]) + len(body)
        ready = stats["files"] > 0
        out = {
            "ok": True, "kind": kind, "ready": ready,
            "reason": None if ready else "아직 인덱스가 없어요. 먼저 동기화하세요(askproject sync).",
            "model": self.model, "provider": self.provider, "materials": stats,
            "estimate_usd": estimate_usd(self.model, prompt_chars, kind, TYPICAL_TURNS[kind]),
            "max_usd": min(BUDGET_USD, estimate_usd(self.model, prompt_chars, kind, MAX_TURNS[kind])),
            "max_turns": MAX_TURNS[kind],
            "relearn_hint": self._relearn_hint(kind),
            "items": len(current_items(kind, text)[1]),
        }
        if self.provider == "subscription":
            out["note"] = "구독 모드라 토큰 과금은 없어요(구독 사용량에서 빠져요). 금액은 api 모드 기준이에요."
        return out

    def _relearn_hint(self, kind: str) -> str | None:
        last = (self.workspace.load_learned().get("last") or {}).get(kind)
        if not last:
            return None
        now = set(_dir_counts(self._files(), depth=2, min_files=3))
        new = sorted(now - set(last.get("dirs") or []))
        if not new:
            return None
        return f"마지막 학습 뒤 새 폴더 {len(new)}개가 생겼어요({', '.join(new[:3])}{' …' if len(new) > 3 else ''}). 다시 학습해 볼까요?"

    async def propose(self, kind: str) -> dict:
        text, body, stats = self._inputs(kind)
        if not stats["files"]:
            raise LearnError("아직 인덱스가 없어요. 먼저 동기화하세요(askproject sync).")
        session = ToolSession()
        req = AgentRequest(
            question=f"아래 재료를 보고 필요한 만큼 도구로 확인한 뒤 {FILES[kind]} 초안 JSON을 내세요.\n\n{body}",
            system_prompt=SYSTEM[kind].replace("{schema}", SCHEMA[kind]),
            tools=learn_tools(session, self.code_tools, self.structure_tools, self.doc_tools),
            max_turns=MAX_TURNS[kind], max_budget_usd=BUDGET_USD, model=self.model, effort=self.effort,
        )
        resp: AgentResponse = await self.runner.run(req)
        if not resp.text:
            raise LearnError(f"AI 초안이 비었어요: {resp.error or '응답 없음'} (비용 ${resp.cost_usd:.3f})")
        draft = parse_draft(kind, resp.text)
        learned = self.workspace.load_learned()
        raw, current = current_items(kind, text)
        files = self._files()
        counter = make_rg_counter(self.repo_path, self.repo) if kind == "glossary" else None
        shared = list(_read_shared(self.workspace)) if kind == "domains" else []
        checked = validate(kind, draft, files, current, learned, counter, shared)
        pid = time.strftime("%Y%m%d-%H%M%S") + f"-{kind}-{secrets.token_hex(3)}"
        proposal = {
            "ok": True, "id": pid, "kind": kind, "created_at": time.time(),
            "commit": self.store.get_repo_commit(self.repo.name), "base_hash": _sha(text),
            "model": resp.model, "provider": self.provider, "cost_usd": round(resp.cost_usd, 4),
            "tokens": {"input": resp.input_tokens, "output": resp.output_tokens,
                       "cache_read": resp.cache_read_tokens, "cache_write": resp.cache_write_tokens},
            "num_turns": resp.num_turns, "duration_ms": resp.duration_ms, "agent_error": resp.error,
            "dirs": sorted(_dir_counts(files, depth=2, min_files=3)),
            **checked,
        }
        self.workspace.save_proposal(proposal)
        return proposal


def _read_shared(ws: Workspace) -> list[str]:
    raw, _ = current_items("domains", _read(ws.file("domains")))
    return [str(x) for x in raw.get("shared") or []]


# ---------- 적용 ----------

def render(kind: str, raw: dict, items: dict[str, Any], header: str) -> str:
    if kind == "domains":
        data = dict(raw) if raw else {"shared": [], "domains": []}
        data.setdefault("shared", [])
        data["domains"] = list(items.values())
    else:
        data = items
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=1000) if data else "{}\n"
    return header + body


def check_loadable(kind: str, text: str) -> None:
    """쓰기 전에 스코핑 로더가 읽을 수 있는지 확인한다."""
    from askproject.pipeline.scoping import parse_domain_map

    raw = yaml.safe_load(text)
    if kind == "domains":
        parse_domain_map(raw, {})
    elif raw is not None and not isinstance(raw, dict):
        raise LearnError("용어 사전 형식이 아니에요.")


def apply(ws: Workspace, proposal_id: str, accept: list[str] | None) -> dict:
    """승인된 항목만 병합해 쓴다. accept가 None이면 기본 선택(default_accept) 항목."""
    proposal = ws.load_proposal(proposal_id)
    kind = proposal["kind"]
    path = ws.file(kind)
    text = _read(path)
    if _sha(text) != proposal["base_hash"]:
        raise LearnError(f"제안을 만든 뒤 {FILES[kind]}가 바뀌었어요(직접 고쳤거나 이미 적용함). 다시 학습해 주세요.")
    learned = ws.load_learned()
    raw, current = current_items(kind, text)
    by_key = {i["key"]: i for i in proposal["items"]}
    wanted = [i["key"] for i in proposal["items"] if i.get("default_accept")] if accept is None else accept
    applied, skipped = [], []
    fps = dict(learned.get(kind) or {})
    for key in dict.fromkeys(wanted):
        item = by_key.get(key)
        if not item or item["action"] in ("same",):
            skipped.append({"key": key, "reason": "적용할 변경이 없어요"})
            continue
        if item["action"] == "locked" or (key in current and lock_reason(kind, key, current[key], learned)):
            skipped.append({"key": key, "reason": f"잠긴 항목({item.get('locked_reason') or '직접 쓴 항목'})이라 바꾸지 않았어요"})
            continue
        if item["action"] == "remove":
            current.pop(key, None)
            fps.pop(key, None)
        else:
            value = item["after"]
            if kind == "domains":
                value = {**value, **({"evidence": item["evidence"]} if item.get("evidence") else {})}
            current[key] = value
            fps[key] = fingerprint(kind, value)
        applied.append({"key": key, "action": item["action"]})
    if not applied:
        return {"ok": True, "kind": kind, "applied": [], "skipped": skipped, "backup": None, "restart_needed": False}
    new_text = render(kind, raw, current, header_comments(text))
    check_loadable(kind, new_text)
    backup = None
    if path.is_file():
        bdir = ws.backup_dir / "config"
        bdir.mkdir(parents=True, exist_ok=True)
        backup = bdir / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
        shutil.copy2(path, backup)
    paths.atomic_write(path, new_text, mode=0o644)
    learned[kind] = fps
    learned.setdefault("last", {})[kind] = {"proposal": proposal_id, "commit": proposal.get("commit"),
                                            "applied_at": time.time(), "dirs": proposal.get("dirs") or []}
    ws.save_learned(learned)
    return {"ok": True, "kind": kind, "applied": applied, "skipped": skipped,
            "backup": str(backup) if backup else None, "restart_needed": True}
