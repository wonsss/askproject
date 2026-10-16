"""GitHub 레포 동기화. git 없이 REST API만 쓴다(제품 맥에 Xcode 명령줄 도구가 없어도 되게).

- 기준 커밋: GET /repos/{o}/{r}/commits/{ref} → SHA. 이전과 같으면 아무것도 받지 않는다.
- 파일 목록·blob 해시: GET /repos/{o}/{r}/git/trees/{sha}?recursive=1 (commit_files 최신성 비교의 기준).
  목록이 잘리면(truncated, 아주 큰 레포) 받은 파일로 blob 해시를 직접 계산한다.
- 내용: GET /repos/{o}/{r}/tarball/{sha}를 새 폴더에 풀고 기존 사본과 바꿔 끼운다(도중에 실패해도 이전 사본 유지).
- 바뀐 파일: 이전·새 트리의 (경로, blob 해시) 비교.

인증 우선순위: GitHub App 설치 토큰 → fine-grained PAT → 인증 없음(공개 레포).
PAT 응답의 만료 헤더(github-authentication-token-expiration)는 data/github_token.json에 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import jwt

from askproject import paths
from askproject.config import RepoConfig
from askproject.settings import Settings

log = logging.getLogger(__name__)

STATE_FILE = ".askproject-sync.json"
EXPIRY_HEADER = "github-authentication-token-expiration"


@dataclass
class SyncResult:
    repo: str
    commit_sha: str
    previous_sha: str | None
    changed_files: list[str] = field(default_factory=list)
    full: bool = False


class GitHubAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def kind(self) -> str:
        s = self.settings
        if s.github_app_id and s.github_app_private_key_path and s.github_app_installation_id:
            return "app"
        return "token" if s.github_token else "none"

    def token(self) -> str | None:
        s = self.settings
        if self.kind == "app":
            if self._token and time.time() < self._expires_at - 120:
                return self._token
            now = int(time.time())
            app_jwt = jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": s.github_app_id},
                s.github_app_private_key_path.read_text(),
                algorithm="RS256",
            )
            resp = httpx.post(
                f"{s.github_api_url}/app/installations/{s.github_app_installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
                timeout=30,
            )
            resp.raise_for_status()
            self._token = resp.json()["token"]
            self._expires_at = time.time() + 3600
            return self._token
        if s.github_token:
            return s.github_token.get_secret_value()
        return None

    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "askproject"}
        token = self.token()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h


def parse_github_url(url: str) -> tuple[str, str]:
    """https://github.com/owner/repo(.git) → (owner, repo). GitHub 주소가 아니면 ValueError."""
    u = urlparse(url.strip())
    parts = [p for p in u.path.split("/") if p]
    if u.scheme != "https" or u.hostname != "github.com" or len(parts) != 2:
        raise ValueError(f"GitHub 레포 주소(https://github.com/조직/레포)가 아니에요: {url}")
    return parts[0], parts[1].removesuffix(".git")


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def parse_expiry(value: str) -> datetime | None:
    """'2026-11-15 10:00:00 UTC' / '… +0000' 형식의 만료 헤더."""
    v = value.strip().replace(" UTC", " +0000")
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(v, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def record_token_expiry(data_dir: Path, response: httpx.Response) -> None:
    """PAT 만료 헤더를 기록한다. 헤더가 없으면(만료 없음·App 토큰) 그 사실을 기록한다."""
    raw = response.headers.get(EXPIRY_HEADER)
    exp = parse_expiry(raw) if raw else None
    try:
        paths.atomic_write(data_dir / "github_token.json",
                           json.dumps({"expires_at": exp.isoformat() if exp else None, "checked_at": time.time()}))
    except OSError:
        log.warning("github_token.json 기록 실패", exc_info=True)


def token_expiry_status(data_dir: Path, warn_days: int = 7) -> dict | None:
    """status --json용: {"expires_at", "days_left", "warning"}. 기록이 없으면 None."""
    try:
        info = json.loads((data_dir / "github_token.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not info.get("expires_at"):
        return {"expires_at": None, "days_left": None, "warning": False}
    exp = datetime.fromisoformat(info["expires_at"])
    days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400
    return {"expires_at": info["expires_at"], "days_left": round(days, 1), "warning": days <= warn_days}


class RepoSync:
    def __init__(self, settings: Settings, auth: GitHubAuth | None = None, client: httpx.Client | None = None):
        self.settings = settings
        self.auth = auth or GitHubAuth(settings)
        self.client = client or httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True)

    def path_for(self, repo: RepoConfig) -> Path:
        return self.settings.repos_dir / repo.name

    def _state_path(self, repo: RepoConfig) -> Path:
        return self.settings.repos_dir / f"{repo.name}{STATE_FILE}"

    def _state(self, repo: RepoConfig) -> dict:
        try:
            return json.loads(self._state_path(repo).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _api(self, path: str, accept: str | None = None, **kw) -> httpx.Response:
        headers = self.auth.headers()
        if accept:
            headers["Accept"] = accept
        r = self.client.get(f"{self.settings.github_api_url}{path}", headers=headers, **kw)
        if self.auth.kind == "token":
            record_token_expiry(self.settings.data_dir, r)
        if r.status_code in (401, 403, 404):
            hint = {401: "토큰이 거부됐어요(만료·폐기 확인)",
                    403: "권한 또는 사용량 한도 문제예요",
                    404: "레포가 없거나 토큰에 이 레포 접근 권한이 없어요(fine-grained PAT의 레포 선택·조직 승인 확인)"}
            raise RuntimeError(f"GitHub {r.status_code}: {hint[r.status_code]}")
        r.raise_for_status()
        return r

    def remote_head(self, repo: RepoConfig) -> str:
        owner, name = parse_github_url(repo.url)
        r = self._api(f"/repos/{owner}/{name}/commits/{repo.ref}", accept="application/vnd.github.sha")
        return r.text.strip()

    def tree(self, repo: RepoConfig) -> list[tuple[str, str]]:
        """현재 사본의 (경로, git blob 해시) 목록."""
        return [tuple(x) for x in self._state(repo).get("tree", [])]

    def head(self, repo: RepoConfig) -> str | None:
        return self._state(repo).get("sha") if self.path_for(repo).is_dir() else None

    def _remote_tree(self, owner: str, name: str, sha: str) -> list[tuple[str, str]] | None:
        data = self._api(f"/repos/{owner}/{name}/git/trees/{sha}", params={"recursive": "1"}).json()
        if data.get("truncated"):
            return None
        return sorted((e["path"], e["sha"]) for e in data.get("tree", []) if e.get("type") == "blob"
                      and e.get("mode") != "120000")  # 심볼릭 링크는 다루지 않는다

    def _download(self, owner: str, name: str, sha: str, dest: Path) -> None:
        """tarball을 dest에 푼다. 일반 파일·폴더만, 레포 밖으로 나가는 경로는 tarfile data 필터가 막는다."""
        with tempfile.TemporaryFile() as buf:
            with self.client.stream("GET", f"{self.settings.github_api_url}/repos/{owner}/{name}/tarball/{sha}",
                                    headers=self.auth.headers()) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes(1 << 20):
                    buf.write(chunk)
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                members = []
                for m in tar.getmembers():
                    top, _, rest = m.name.partition("/")  # 최상위 '{owner}-{repo}-{sha}/' 제거
                    norm = posixpath.normpath(rest) if rest else ""
                    if not rest or not (m.isfile() or m.isdir()) or norm.startswith(("..", "/")):
                        continue
                    m.name = rest
                    members.append(m)
                tar.extractall(dest, members=members, filter="data")

    @staticmethod
    def _local_tree(root: Path) -> list[tuple[str, str]]:
        rows = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.is_symlink():
                rows.append((p.relative_to(root).as_posix(), git_blob_sha(p.read_bytes())))
        return rows

    def sync(self, repo: RepoConfig) -> SyncResult:
        owner, name = parse_github_url(repo.url)
        path = self.path_for(repo)
        state = self._state(repo) if path.is_dir() else {}
        previous = state.get("sha")
        sha = self.remote_head(repo)
        if sha == previous:
            return SyncResult(repo.name, sha, previous)

        log.info("downloading %s@%s (%s)", repo.name, repo.ref, sha[:7])
        self.settings.repos_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{repo.name}-", dir=self.settings.repos_dir))
        try:
            self._download(owner, name, sha, staging)
            new_tree = self._remote_tree(owner, name, sha) or self._local_tree(staging)
            old = path.with_name(f".{repo.name}-old")
            shutil.rmtree(old, ignore_errors=True)
            if path.exists():
                path.rename(old)
            staging.rename(path)
            shutil.rmtree(old, ignore_errors=True)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        paths.atomic_write(self._state_path(repo), json.dumps({"sha": sha, "ref": repo.ref, "tree": new_tree}),
                           mode=0o644)

        if not previous:
            return SyncResult(repo.name, sha, None, full=True)
        before, after = dict(state.get("tree", [])), dict(new_tree)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        return SyncResult(repo.name, sha, previous, changed_files=changed)

    def check_access(self, repo: RepoConfig) -> str:
        """doctor용: 기준 브랜치 접근 확인. 성공하면 커밋 SHA 앞 7자리."""
        return self.remote_head(repo)[:7]
