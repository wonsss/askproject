from askproject.tools.code_tools import ToolSession


def test_index_excludes_secrets_binaries_and_archive(store, tools, repo_cfg):
    paths = {r["path"] for r in store.execute("SELECT path FROM files WHERE repo=?", (repo_cfg.name,))}
    assert "apps/api/.env" not in paths
    assert "apps/api/.env.example" in paths
    assert "archive/old.ts" not in paths
    assert "assets/logo.png" not in paths
    assert "apps/web/lib/bridge.ts" in paths


def test_search_finds_korean_and_marks_session(tools):
    s = ToolSession()
    out = tools.search(s, "의안 상세")
    assert "apps/api/src/bills/bills.controller.ts:5" in out
    assert s.covers("apps/api/src/bills/bills.controller.ts", 5, 5)


def test_search_never_returns_secret_file(tools):
    out = tools.search(ToolSession(), "do-not-read")
    assert "결과 없음" in out and ".env:" not in out


def test_read_clamps_and_rejects(tools):
    s = ToolSession()
    out = tools.read(s, "apps/api/src/bills/bills.controller.ts", 3, 999)
    assert "[3-8 / 8줄]" in out
    assert s.covers("apps/api/src/bills/bills.controller.ts", 4, 6)
    assert "허용되지 않은" in tools.read(s, "apps/api/.env")
    assert "인덱스에 없는" in tools.read(s, "archive/old.ts") or "허용되지 않은" in tools.read(s, "archive/old.ts")


def test_list_dir_groups_one_level(tools):
    out = tools.list_dir(ToolSession(), "apps")
    assert "apps/api/" in out and "apps/web/" in out


def test_read_survives_file_shorter_than_index(tools):
    """동기화 직후 파일이 인덱스의 줄 수보다 짧아져도 IndexError로 죽지 않는다."""
    p = "apps/api/src/bills/bills.controller.ts"
    (tools.repo_path / p).write_text("one\ntwo\n", encoding="utf-8")
    out = tools.read(ToolSession(), p, 1, 999)
    assert "    2  two" in out and "IndexError" not in out


def test_list_dir_treats_underscore_literally(tools):
    """LIKE의 _·% 와일드카드 때문에 다른 폴더가 섞이지 않는다."""
    assert "인덱싱된 파일이 없습니다" in tools.list_dir(ToolSession(), "app_")
