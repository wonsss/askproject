"""레포 레지스트리(config/repos.yaml) 로딩."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_BAD_REF_RE = re.compile(r"[\s\x00-\x1f~^:?*\[\\]")


@dataclass(frozen=True)
class RepoConfig:
    name: str
    url: str
    ref: str = "main"
    description: str = ""
    exclude: tuple[str, ...] = ()
    include_exceptions: tuple[str, ...] = ()
    context_files: tuple[str, ...] = field(default_factory=tuple)
    """시스템 프롬프트에 레포 개요로 넣을 파일(예: CLAUDE.md)."""
    structure: dict = field(default_factory=dict)
    """코드 구조 맵 추출 규칙(indexing/structure.StructureConfig 필드와 같은 키). 비어 있으면 구조 인덱싱을 건너뛴다."""

    def __post_init__(self) -> None:
        # name은 data/repos 아래 폴더 이름, ref는 GitHub API 경로에 들어간다. 손으로 고친 repos.yaml도 여기서 막는다
        # ('..'이면 동기화가 data 폴더를 옮기고 지울 수 있다).
        if not _NAME_RE.fullmatch(self.name) or self.name in {".", ".."}:
            raise ValueError(f"레포 이름 형식이 아니에요(영문·숫자·._-): {self.name!r}")
        if not self.ref or ".." in self.ref or self.ref.startswith("/") or _BAD_REF_RE.search(self.ref):
            raise ValueError(f"ref 형식이 아니에요: {self.ref!r}")

    @property
    def web_base(self) -> str:
        """인용 링크용 GitHub 웹 주소."""
        return self.url.removesuffix(".git")


def load_repos(config_dir: Path) -> list[RepoConfig]:
    raw = yaml.safe_load((config_dir / "repos.yaml").read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults", {})
    repos: list[RepoConfig] = []
    for item in raw.get("repos", []):
        exclude = [*defaults.get("exclude", []), *item.get("exclude", [])]
        include_exceptions = [*defaults.get("include_exceptions", []), *item.get("include_exceptions", [])]
        repos.append(
            RepoConfig(
                name=item["name"],
                url=item["url"],
                ref=item.get("ref", "main"),
                description=item.get("description", ""),
                exclude=tuple(exclude),
                include_exceptions=tuple(include_exceptions),
                context_files=tuple(item.get("context_files", [])),
                structure=dict(item.get("structure") or {}),
            )
        )
    if not repos:
        raise ValueError("config/repos.yaml에 레포가 없습니다.")
    return repos
