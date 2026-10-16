"""레포 레지스트리(config/repos.yaml) 로딩."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
