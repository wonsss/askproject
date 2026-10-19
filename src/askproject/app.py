"""조립 지점(composition root). 구현체 선택은 여기서만 한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from askproject.agent.claude_runner import ClaudeAgentRunner
from askproject.config import RepoConfig, load_repos
from askproject.indexing.code_index import build_code_index
from askproject.indexing.docs_index import collect_repo_docs, store_virtual_docs
from askproject.indexing.repo_sync import RepoSync
from askproject.indexing.structure import StructureConfig, build_structure, build_structure_multi
from askproject.llm.complete import Completer, LLM
from askproject.pipeline.answer import AnswerService
from askproject.pipeline.scoping import DomainMap, Scoper, load_domain_map
from askproject.repo_set import RepoSet
from askproject.settings import Settings
from askproject.store.db import Store
from askproject.tools.code_tools import CodeTools
from askproject.tools.doc_tools import DocTools
from askproject.tools.structure_tools import StructureTools

log = logging.getLogger(__name__)

LAYOUT_KEY = "#layout"
"""repo_state에 남기는 레포 구성(이름 목록). 구성이 바뀌면 구조 맵·레포 문서를 다시 만든다."""


@dataclass
class RefreshReport:
    commit: str
    previous: str | None
    changed_files: int
    reindexed: bool
    stats: dict = field(default_factory=dict)
    repos: dict[str, dict] = field(default_factory=dict)
    """레포 이름 → {commit, previous, changed_files, reindexed} (여러 레포일 때 레포별 결과)."""


@dataclass
class Container:
    settings: Settings
    store: Store
    repos: RepoSet
    sync: RepoSync
    service: AnswerService
    llm: Completer
    domain_map: DomainMap

    @property
    def repo(self) -> RepoConfig:
        """첫 번째 레포(레포 하나를 전제한 호출부용)."""
        return self.repos.primary

    def refresh(self, *, external: bool = True, force: bool = False) -> RefreshReport:
        """레포마다 동기화 → (하나라도 바뀌면) 코드 인덱스·구조 맵·레포 문서 재생성 → 노션·슬랙 수집."""
        rs, stats, per_repo, any_reindex = self.repos, {}, {}, False
        for repo in rs.repos:
            result = self.sync.sync(repo)
            reindex = force or result.full or bool(result.changed_files) or self.store.file_count(repo.name) == 0
            if reindex:
                idx = build_code_index(self.store, repo, self.sync.path_for(repo), [p for p, _ in self.sync.tree(repo)])
                stats.setdefault("code", {})[repo.name] = idx.__dict__
            any_reindex = any_reindex or reindex
            self.store.set_repo_state(repo.name, result.commit_sha)
            if not self.store.has_commit_files(repo.name, result.commit_sha):
                self.store.record_commit_files(repo.name, result.commit_sha, self.sync.tree(repo))
            per_repo[repo.name] = {"commit": result.commit_sha, "previous": result.previous_sha,
                                   "changed_files": len(result.changed_files), "reindexed": reindex}
        if "code" in stats and not rs.multi:  # 레포 하나: 기존 통계 모양을 유지한다
            stats["code"] = stats["code"][rs.primary.name]
        # 레포 구성이 바뀌면(추가·삭제) 바뀐 파일이 없어도 경로 공간이 달라지므로 다시 만든다
        layout = ",".join(r.name for r in rs.repos)
        if any_reindex or self.store.get_repo_commit(LAYOUT_KEY) != layout:
            stats.update(self._rebuild_derived())
            self.store.set_repo_state(LAYOUT_KEY, layout)
        if external:
            stats.update(self.refresh_external())
        first = per_repo[rs.primary.name]
        return RefreshReport(
            rs.commit_value(self.store) or first["commit"], first["previous"],
            sum(r["changed_files"] for r in per_repo.values()), any_reindex, stats, per_repo if rs.multi else {})

    def _rebuild_derived(self) -> dict:
        """구조 맵·레포 문서는 레포를 모두 모아 다시 만든다(레포를 가로지르는 API 연결 때문에)."""
        rs, out = self.repos, {}
        files = {r.name: [x["path"] for x in self.store.execute("SELECT path FROM files WHERE repo=?", (r.name,))]
                 for r in rs.repos}
        if rs.multi:
            parts = [(r, self.sync.path_for(r), files[r.name], StructureConfig.from_dict(r.structure))
                     for r in rs.repos if r.structure]
            if parts:
                out["structure"] = build_structure_multi(self.store, rs, parts)
        elif rs.primary.structure:
            r = rs.primary
            out["structure"] = build_structure(self.store, r.name, self.sync.path_for(r), files[r.name],
                                               StructureConfig.from_dict(r.structure))
        docs, chunks = [], []
        for r in rs.repos:
            sha = self.store.get_repo_commit(r.name) or r.ref
            d, c = collect_repo_docs(self.sync.path_for(r), files[r.name], r.web_base, sha, rs.filter(r),
                                     prefix=rs.vpath(r, ""))
            docs += d
            chunks += c
        self.store.replace_docs("repo", docs, chunks)
        out["repo_docs"] = len(docs)
        return out

    def refresh_external(self) -> dict:
        s, out = self.settings, {}
        if s.notion_token and s.notion_root_page_id:
            from askproject.indexing.notion_source import NotionSource
            try:
                items = NotionSource(s.notion_token.get_secret_value(), s.notion_root_page_id).collect()
                out["notion_pages"] = store_virtual_docs(self.store, "notion", items)
            except Exception as exc:
                log.exception("notion sync failed")
                out["notion_error"] = str(exc)[:200]
        channels = [c.strip() for c in s.slack_source_channels.split(",") if c.strip()]
        if s.slack_bot_token and channels:
            from askproject.indexing.slack_source import SlackSource
            try:
                items = SlackSource(s.slack_bot_token.get_secret_value(), channels, s.slack_source_days).collect()
                out["slack_threads"] = store_virtual_docs(self.store, "slack", items)
            except Exception as exc:
                log.exception("slack source sync failed")
                out["slack_error"] = str(exc)[:200]
        return out


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings()
    store = Store(settings.db_path)
    repos = RepoSet.from_repos(load_repos(settings.config_dir), settings.repos_dir)
    sync = RepoSync(settings)
    subscription = settings.llm_provider == "subscription"
    api_key = None if subscription else (
        settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None)
    if subscription:
        from askproject.llm.claude_cli import CliLLM  # 구독 로그인: claude CLI 하위 프로세스(로컬 PoC 전용)
        llm: Completer = CliLLM(settings)
    else:
        llm = LLM(settings)
    domain_map = load_domain_map(settings.config_dir)
    runner = ClaudeAgentRunner(model=settings.model_large, api_key=api_key,
                               base_url=None if subscription else settings.anthropic_base_url,
                               subscription=subscription,
                               config_dir=None if subscription else str(settings.claude_config_dir.resolve()))
    code_tools = CodeTools(store, repos, max_results=settings.search_max_results, max_lines=settings.read_max_lines)
    service = AnswerService(
        store, repos, None, runner, code_tools, settings.agent_max_turns, settings.agent_max_budget_usd,
        scoper=Scoper(llm, domain_map), domain_map=domain_map, doc_tools=DocTools(store),
        structure_tools=StructureTools(store, repos.structure_key, repos.root),
        models={"small": settings.model_small, "large": settings.model_large},
        efforts={"small": settings.agent_effort_small or None, "large": settings.agent_effort_large or None},
        provider=settings.llm_provider,
    )
    return Container(settings, store, repos, sync, service, llm, domain_map)
