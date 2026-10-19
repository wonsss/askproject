"""여러 대상 레포: 경로 공간(`<레포>/…`), 도구, 인용 검증, 구조 맵, 동기화, 콘솔 최신성, 정리."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from askproject.agent.base import AgentRequest, AgentResponse
from askproject.app import Container
from askproject.config import RepoConfig, load_repos
from askproject.config_store import ConfigPatch
from askproject.console.data import ConsoleData
from askproject.indexing.code_index import build_code_index
from askproject.indexing.repo_sync import SyncResult
from askproject.indexing.structure import StructureConfig, build_structure_multi
from askproject.ops import maintenance
from askproject.pipeline.answer import AnswerService
from askproject.repo_set import RepoSet, format_commit
from askproject.store.db import Store
from askproject.tools.code_tools import CodeTools, ToolSession
from askproject.tools.structure_tools import StructureTools

API_FILES = {
    "src/orders/orders.controller.ts": (
        "@Controller('orders')\nexport class OrdersController {\n  @Get(':id')\n  findOne() {\n"
        "    // 주문 상세 조회\n    return this.service.findOne()\n  }\n}\n"
    ),
    "docs/orders.md": "# 주문 정책\n\n취소는 배송 전까지만 된다.\n",
    "secret/keys.ts": "export const KEY = 'x'\n",
}
WEB_FILES = {
    "lib/routes.ts": "export const api = {\n  getOrder: (id) =>\n    client.get(`${V1}/orders/${id}`),\n}\n",
    "app/orders/[id]/page.tsx": "// 주문 상세 화면\nexport default function Page() {}\n",
    "README.md": "# 웹\n",
}
API = RepoConfig("api", "https://github.com/example/api", exclude=("secret/**",),
                 structure={"controller_glob": "src/**/*.controller.ts", "api_prefix": "/api/v1"})
WEB = RepoConfig("web", "https://github.com/example/web", ref="dev",
                 structure={"api_client_files": ["lib/routes.ts"], "api_prefix": "/api/v1", "web_app_dir": "app"})


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@pytest.fixture
def rs(tmp_path: Path) -> RepoSet:
    _write(tmp_path / "repos" / "api", API_FILES)
    _write(tmp_path / "repos" / "web", WEB_FILES)
    (tmp_path / "repos" / "api.sync.json").write_text("{}")  # 체크아웃 밖 파일은 검색하지 않는다
    return RepoSet.from_repos([API, WEB], tmp_path / "repos")


@pytest.fixture
def indexed(store: Store, rs: RepoSet) -> Store:
    build_code_index(store, API, rs.checkout(API), list(API_FILES))
    build_code_index(store, WEB, rs.checkout(WEB), list(WEB_FILES))
    store.set_repo_state("api", "aaaa1111")
    store.set_repo_state("web", "bbbb2222")
    return store


def test_repo_set_paths_commits_and_links(rs):
    single = RepoSet.from_repos([API], rs.root)
    assert single.vpath(API, "src/a.ts") == "src/a.ts" and single.split("src/a.ts") == (API, "src/a.ts")
    assert rs.multi and rs.vpath(WEB, "lib/x.ts") == "web/lib/x.ts"
    assert rs.split("web/lib/x.ts") == (WEB, "lib/x.ts")
    assert rs.split("lib/x.ts") is None and rs.split("nope/x.ts") is None
    assert not rs.allowed("api/secret/keys.ts") and rs.allowed("api/src/orders/orders.controller.ts")

    value = "api@aaaa1111,web@bbbb2222"
    assert rs.commits(value) == {"api": "aaaa1111", "web": "bbbb2222"}
    assert rs.commits("cccc3333") == {"api": "cccc3333"}  # 레포 하나일 때 남은 기록은 첫 레포의 커밋
    assert rs.url("web/lib/routes.ts", value, 3, 5) == "https://github.com/example/web/blob/bbbb2222/lib/routes.ts#L3-L5"
    assert rs.url("api/src", value) == "https://github.com/example/api/tree/aaaa1111/src"
    assert rs.url("web/lib/routes.ts", None, 1) == "https://github.com/example/web/blob/dev/lib/routes.ts#L1"
    assert rs.scope_globs(("src/orders/**", "web/app/**", "**/*.md")) == ("api/src/orders/**", "web/app/**", "**/*.md")
    assert single.scope_globs(("src/**",)) == ("src/**",)
    assert format_commit("abcdef12345") == "main@abcdef1"
    assert format_commit(value) == "api@aaaa111 · web@bbbb222"


def test_code_tools_search_read_list_across_repos(indexed, rs):
    ct = CodeTools(indexed, rs)
    s = ToolSession()
    out = ct.search(s, "주문 상세")
    assert "api/src/orders/orders.controller.ts:5:" in out and "web/app/orders/[id]/page.tsx:1:" in out
    assert "keys.ts" not in ct.search(s, "KEY")  # 레포별 제외 규칙
    assert "api.sync.json" not in ct.search(s, "{}")
    assert "[1-8 / 8줄]" in ct.read(s, "api/src/orders/orders.controller.ts")
    assert "레포 이름으로 시작" in ct.read(s, "src/orders/orders.controller.ts")
    assert "접근이 허용되지 않은" in ct.read(s, "api/secret/keys.ts")
    top = ct.list_dir(s, "")
    assert "api/  (2개 파일)" in top and "web/  (3개 파일)" in top
    assert "web/lib/routes.ts" in ct.list_dir(s, "web/lib")
    assert "api/src/orders/" in ct.list_dir(s, "api/src")


class CrossRepoRunner:
    def __init__(self):
        self.request: AgentRequest | None = None

    async def run(self, request: AgentRequest) -> AgentResponse:
        self.request = request
        tools = {t.name: t for t in request.tools}
        await tools["read_code"].handler({"path": "api/src/orders/orders.controller.ts", "start": 1, "end": 8})
        await tools["read_code"].handler({"path": "web/lib/routes.ts"})
        text = ("**주문 상세는 웹이 서버에 물어봐요.**\n\n근거:\n"
                "- web/lib/routes.ts:2-3 — 상세 요청\n"
                "- api/src/orders/orders.controller.ts:3-6 — 상세 조회\n"
                "- src/orders/orders.controller.ts:3 — 레포 이름이 빠진 경로\n")
        return AgentResponse(text=text, model="fake", cost_usd=0.01, num_turns=2)


async def test_answer_verifies_citations_per_repo(indexed, rs):
    runner = CrossRepoRunner()
    svc = AnswerService(indexed, rs, None, runner, CodeTools(indexed, rs))
    a = await svc.answer("주문 상세는 어떻게 불러와?")
    assert a.commit == "api@aaaa1111,web@bbbb2222"
    urls = {c.path: c.url for c in a.citations}
    assert urls == {
        "web/lib/routes.ts": "https://github.com/example/web/blob/bbbb2222/lib/routes.ts#L2-L3",
        "api/src/orders/orders.controller.ts": "https://github.com/example/api/blob/aaaa1111/src/orders/orders.controller.ts#L3-L6",
    }
    assert a.confidence == "medium"  # 레포 이름 없는 인용은 검증 실패
    assert [c.reason for c in a.unverified] == ["인덱스에 없는 파일"]
    prompt = runner.request.system_prompt
    assert "## 대상 레포 (여러 개)" in prompt and "web: (설명 없음) — dev 브랜치 커밋 bbbb222" in prompt
    logged = indexed.execute("SELECT commit_sha FROM queries WHERE id=?", (a.query_id,)).fetchone()[0]
    assert logged == "api@aaaa1111,web@bbbb2222"


def test_structure_links_client_and_controller_across_repos(indexed, rs):
    parts = [(r, rs.checkout(r), list(f), StructureConfig.from_dict(r.structure))
             for r, f in ((API, API_FILES), (WEB, WEB_FILES))]
    counts = build_structure_multi(indexed, rs, parts)
    assert counts["api_linked"] == 1 and counts["route"] == 1
    st = StructureTools(indexed, rs.structure_key, rs.root)
    s = ToolSession()
    out = st.find_api(s, "orders")
    assert "web/lib/routes.ts:3" in out and "api/src/orders/orders.controller.ts:3" in out
    assert s.covers("api/src/orders/orders.controller.ts", 3, 4)
    screen = st.find_screen(s, "orders")
    assert "[web 라우트] /orders/:id — web/app/orders/[id]/page.tsx" in screen


class FakeSync:
    def __init__(self, repos_dir: Path, shas: dict[str, str], files: dict[str, dict]):
        self.repos_dir, self.shas, self.files, self.calls = repos_dir, shas, files, []

    def path_for(self, repo):
        return self.repos_dir / repo.name

    def tree(self, repo):
        return [(p, f"blob-{p}") for p in self.files[repo.name]]

    def sync(self, repo):
        self.calls.append(repo.name)
        return SyncResult(repo.name, self.shas[repo.name], None, list(self.files[repo.name]), full=True)


def test_refresh_syncs_every_repo_and_prefixes_docs(store, rs):
    sync = FakeSync(rs.root, {"api": "aaaa1111", "web": "bbbb2222"}, {"api": API_FILES, "web": WEB_FILES})
    svc = AnswerService(store, rs, None, CrossRepoRunner(), CodeTools(store, rs))
    c = Container(None, store, rs, sync, svc, None, None)
    r = c.refresh(external=False)
    assert sync.calls == ["api", "web"] and r.commit == "api@aaaa1111,web@bbbb2222"
    assert set(r.repos) == {"api", "web"} and r.stats["structure"]["api_linked"] == 1
    docs = {d["path"]: d["url"] for d in store.execute("SELECT path, url FROM doc_files")}
    assert docs == {"api/docs/orders.md": "https://github.com/example/api/blob/aaaa1111/docs/orders.md",
                    "web/README.md": "https://github.com/example/web/blob/bbbb2222/README.md"}
    assert store.has_commit_files("web", "bbbb2222")
    # 레포를 하나로 줄이면 바뀐 파일이 없어도 경로 공간이 달라져 문서·구조를 다시 만든다
    one = RepoSet.from_repos([API], rs.root)
    sync1 = FakeSync(rs.root, {"api": "aaaa1111"}, {"api": API_FILES})
    sync1.sync = lambda repo: SyncResult(repo.name, "aaaa1111", "aaaa1111", [], full=False)
    c1 = Container(None, store, one, sync1, AnswerService(store, one, None, CrossRepoRunner(), CodeTools(store, one)),
                   None, None)
    r1 = c1.refresh(external=False)
    assert not r1.reindexed and r1.commit == "aaaa1111"
    assert {d["path"] for d in store.execute("SELECT path FROM doc_files")} == {"docs/orders.md"}


def test_console_freshness_and_links_per_repo(tmp_path, indexed, rs):
    st = indexed
    for repo, sha, blob in (("api", "aaaa1111", "x1"), ("web", "bbbb2222", "y1"), ("web", "bbbb3333", "y2"),
                            ("api", "aaaa9999", "x1")):
        st.record_commit_files(repo, sha, [("lib/routes.ts" if repo == "web" else "src/orders/orders.controller.ts", blob)])
    qid = st.log_query("q", "slack", "U1", "C1", None, "api@aaaa1111,web@bbbb2222")
    for path, line in (("web/lib/routes.ts", 2), ("api/src/orders/orders.controller.ts", 3)):
        st.log_citation(qid, "code", path, line, line, True, "ok")
    data = ConsoleData(tmp_path / "db.sqlite3", "api", API.web_base, tmp_path, repos=rs)
    row = data.get(qid)
    cites = data.citations(row)
    assert cites[0]["url"] == "https://github.com/example/web/blob/bbbb2222/lib/routes.ts#L2"
    assert data.freshness(row, cites).state == "ok"
    st.set_repo_state("web", "bbbb3333")  # web만 근거 파일이 바뀌었다
    st.set_repo_state("api", "aaaa9999")  # api는 커밋만 바뀌고 파일은 그대로
    f = data.freshness(row, cites)
    assert f.state == "changed" and f.changed == ["web/lib/routes.ts"]


def test_maintenance_keeps_snapshots_named_in_multi_commit(tmp_path):
    st = Store(tmp_path / "db.sqlite3")
    st.log_query("q", "slack", None, None, None, "api@a1,web@w1")
    st.set_repo_state("api", "a3")
    st.set_repo_state("web", "w3")
    for repo, shas in (("api", ("a1", "a2", "a3")), ("web", ("w1", "w2", "w3"))):
        for sha in shas:
            st.record_commit_files(repo, sha, [("f.ts", sha)])
    conn = sqlite3.connect(tmp_path / "db.sqlite3", isolation_level=None)
    r = maintenance(conn, ["api", "web"])
    left = {(x[0], x[1]) for x in conn.execute("SELECT DISTINCT repo, commit_sha FROM commit_files")}
    assert left == {("api", "a1"), ("api", "a3"), ("web", "w1"), ("web", "w3")} and r["snapshots_dropped"] == 2


def test_duplicate_repo_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="겹쳐요"):
        ConfigPatch.model_validate({"repos": [{"name": "api", "url": "https://github.com/a/api"},
                                              {"name": "api", "url": "https://github.com/b/api"}]})
    (tmp_path / "repos.yaml").write_text("repos:\n  - {name: api, url: https://github.com/a/api}\n"
                                         "  - {name: api, url: https://github.com/b/api}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="같은 레포 이름"):
        load_repos(tmp_path)
