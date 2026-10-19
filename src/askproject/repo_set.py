"""여러 대상 레포를 하나의 경로 공간으로 묶는다.

- 레포가 하나면 경로는 레포 안 경로 그대로다(기존 인용·감사 로그·골든 세트와 호환).
- 여러 개면 `<레포 이름>/<레포 안 경로>`로 쓴다. 체크아웃이 `data/repos/<이름>/`에 있으므로
  `data/repos`가 곧 가상 루트가 된다(검색·읽기는 그 폴더에서 그대로 돈다).

답변 기준 커밋은 한 문자열로 남긴다. 하나면 sha, 여러 개면 `이름@sha,이름@sha`.
레포를 하나에서 여러 개로 늘리기 전에 남은 sha만 있는 기록은 첫 번째 레포의 커밋으로 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from askproject.config import RepoConfig
from askproject.indexing.filters import PathFilter

def format_commit(value: str | None, branch: str = "main") -> str:
    """표시용 기준 커밋: 하나면 `main@abc1234`, 여러 개면 `api@abc1234 · web@def5678`."""
    if not value:
        return f"{branch}@"
    if "@" not in value:
        return f"{branch}@{value[:7]}"
    return " · ".join(f"{n}@{sha[:7]}" for n, _, sha in (p.rpartition("@") for p in value.split(",")) if n)


STRUCTURE_KEY = "*"
"""여러 레포일 때 구조 맵을 저장하는 repo 키(레포를 가로질러 API를 잇기 때문에 하나로 저장한다)."""


@dataclass(frozen=True)
class RepoSet:
    repos: tuple[RepoConfig, ...]
    root: Path
    """레포 하나: 그 체크아웃 폴더. 여러 개: 체크아웃들의 상위 폴더(data/repos)."""

    def __post_init__(self) -> None:
        if not self.repos:
            raise ValueError("레포가 없습니다.")

    @classmethod
    def single(cls, repo: RepoConfig, path: Path) -> "RepoSet":
        return cls((repo,), path.resolve())

    @classmethod
    def from_repos(cls, repos: list[RepoConfig], repos_dir: Path) -> "RepoSet":
        if len(repos) == 1:
            return cls.single(repos[0], repos_dir / repos[0].name)
        return cls(tuple(repos), repos_dir.resolve())

    @property
    def multi(self) -> bool:
        return len(self.repos) > 1

    @property
    def primary(self) -> RepoConfig:
        return self.repos[0]

    @property
    def structure_key(self) -> str:
        return STRUCTURE_KEY if self.multi else self.primary.name

    def checkout(self, repo: RepoConfig) -> Path:
        return self.root / repo.name if self.multi else self.root

    def filter(self, repo: RepoConfig) -> PathFilter:
        return PathFilter(repo.exclude, repo.include_exceptions)

    # ---------- 경로 ----------
    def vpath(self, repo: RepoConfig, rel: str) -> str:
        return f"{repo.name}/{rel}" if self.multi else rel

    def split(self, vpath: str) -> tuple[RepoConfig, str] | None:
        """가상 경로 → (레포, 레포 안 경로). 여러 레포인데 레포 이름으로 시작하지 않으면 None."""
        vpath = vpath.removeprefix("./")
        if not self.multi:
            return self.primary, vpath
        name, sep, rel = vpath.partition("/")
        repo = next((r for r in self.repos if r.name == name), None)
        return (repo, rel) if repo and (sep or not rel) else None

    def allowed(self, vpath: str) -> bool:
        hit = self.split(vpath)
        return bool(hit and hit[1] and self.filter(hit[0]).allowed(hit[1]))

    def scope_globs(self, globs: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """도메인 경로 패턴을 가상 경로에 맞춘다. 여러 레포인데 레포 이름으로 시작하지 않는 패턴은
        첫 번째 레포 것으로 본다(레포를 하나 쓰다가 늘린 domains.yaml). `**/`로 시작하면 모든 레포에 맞는다."""
        if not self.multi or globs is None:
            return globs
        names = {r.name for r in self.repos}
        return tuple(g if g.startswith("**") or g.split("/", 1)[0] in names else f"{self.primary.name}/{g}"
                     for g in globs)

    def files(self, store) -> list[str]:
        """인덱스에 있는 모든 파일의 가상 경로(정렬)."""
        out = []
        for repo in self.repos:
            out += [self.vpath(repo, r["path"])
                    for r in store.execute("SELECT path FROM files WHERE repo=? ORDER BY path", (repo.name,))]
        return out

    def file_info(self, store, vpath: str):
        hit = self.split(vpath)
        return store.file_info(hit[0].name, hit[1]) if hit and hit[1] else None

    # ---------- 커밋 ----------
    def commit_value(self, store) -> str | None:
        """지금 인덱스의 기준 커밋(감사 로그 queries.commit_sha에 남기는 값)."""
        if not self.multi:
            return store.get_repo_commit(self.primary.name)
        parts = [f"{r.name}@{sha}" for r in self.repos if (sha := store.get_repo_commit(r.name))]
        return ",".join(parts) or None

    def commits(self, value: str | None) -> dict[str, str]:
        """commit_value를 레포 이름 → sha로 푼다."""
        if not value:
            return {}
        if "@" not in value:
            return {self.primary.name: value}
        out = {}
        for part in value.split(","):
            name, _, sha = part.rpartition("@")
            if name and sha:
                out[name] = sha
        return out

    # ---------- 링크 ----------
    def url(self, vpath: str, commit: str | None, start: int = 0, end: int = 0) -> str:
        """GitHub 링크. start가 0이면 폴더(tree) 링크."""
        hit = self.split(vpath)
        if not hit:
            return ""
        repo, rel = hit
        sha = self.commits(commit).get(repo.name) or repo.ref
        if not start:
            return f"{repo.web_base}/tree/{sha}/{rel}"
        return f"{repo.web_base}/blob/{sha}/{rel}#L{start}" + (f"-L{end}" if end and end != start else "")
