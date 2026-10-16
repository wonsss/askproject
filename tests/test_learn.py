"""도메인·용어 학습: 가짜 AgentRunner로 초안 → 검증 → 적용 → 잠금 보존 (API 호출 없음)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from askproject import paths
from askproject.agent.base import AgentRequest, AgentResponse
from askproject.learn import LearnError, Learner, Workspace, apply, estimate_usd, header_comments
from askproject.pipeline.scoping import load_domain_map

DOMAINS_TEMPLATE = (Path(__file__).parents[1] / "src/askproject/defaults/domains.yaml").read_text(encoding="utf-8")
GLOSSARY_TEMPLATE = (Path(__file__).parents[1] / "src/askproject/defaults/glossary.yaml").read_text(encoding="utf-8")

DOMAIN_DRAFT = {"domains": [
    {"id": "bills", "name": "의안", "summary": "의안 상세", "terms": ["의안"],
     "paths": ["apps/api/src/bills/**", "nope/**", "**"], "evidence": ["API GET /bills/:slug"]},
    {"id": "bridge", "name": "웹뷰 연동", "terms": ["웹뷰"], "paths": ["apps/web/**"]},
    {"id": "ghost", "name": "없는 기능", "paths": ["missing/**"]},
    {"id": "Bad Id", "name": "형식 오류", "paths": ["apps/**"]},
]}


class FakeRunner:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request: AgentRequest | None = None

    async def run(self, request: AgentRequest) -> AgentResponse:
        self.request = request
        tools = {t.name: t for t in request.tools}
        assert "apps/" in await tools["list_dir"].handler({"prefix": ""})
        text = "초안입니다.\n```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"
        return AgentResponse(text=text, model="claude-sonnet-5-5", input_tokens=1000, output_tokens=500,
                             cost_usd=0.12, num_turns=4)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "domains.yaml").write_text(DOMAINS_TEMPLATE, encoding="utf-8")
    (cfg / "glossary.yaml").write_text(GLOSSARY_TEMPLATE, encoding="utf-8")
    return Workspace(cfg, tmp_path / "data", tmp_path / "backups")


def make_learner(store, repo_cfg, repo_dir, tools, ws, payload) -> Learner:
    return Learner(store, repo_cfg, repo_dir, FakeRunner(payload), tools, ws, model="claude-sonnet-5-5")


async def test_propose_validates_paths_and_diff(store, repo_cfg, repo_dir, tools, ws):
    p = await make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT).propose("domains")
    items = {i["key"]: i for i in p["items"]}
    assert set(items) == {"bills", "bridge"}
    assert items["bills"]["action"] == "add" and items["bills"]["default_accept"]
    assert items["bills"]["after"]["paths"] == ["apps/api/src/bills/**"]  # 0매치·전체 패턴은 버림
    assert any("nope/**" in w and "0개" in w for w in items["bills"]["warnings"])
    assert any("레포 전체" in w for w in items["bills"]["warnings"])
    assert {r["key"] for r in p["rejected"]} == {"ghost", "bad id"}
    assert p["coverage"]["before"]["uncovered_ratio"] > p["coverage"]["after"]["uncovered_ratio"]
    assert p["cost_usd"] == 0.12
    assert (ws.learn_dir / "proposals" / f"{p['id']}.json").is_file()


async def test_apply_only_accepted_keeps_header_and_backs_up(store, repo_cfg, repo_dir, tools, ws):
    p = await make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT).propose("domains")
    out = apply(ws, p["id"], ["bills"])
    assert [a["key"] for a in out["applied"]] == ["bills"]
    text = ws.file("domains").read_text(encoding="utf-8")
    assert text.startswith(header_comments(DOMAINS_TEMPLATE))
    assert Path(out["backup"]).read_text(encoding="utf-8") == DOMAINS_TEMPLATE
    raw = yaml.safe_load(text)
    assert raw["shared"] == ["CLAUDE.md", "AGENTS.md", "docs/**"]
    assert [d["id"] for d in raw["domains"]] == ["bills"]
    assert raw["domains"][0]["evidence"] == ["API GET /bills/:slug"]
    dm = load_domain_map(ws.config_dir)  # 새 필드(evidence)를 견딘다
    assert dm.get("bills").paths == ("apps/api/src/bills/**",)
    # 같은 제안은 다시 적용되지 않는다(파일이 바뀌었으므로)
    with pytest.raises(LearnError):
        apply(ws, p["id"], ["bridge"])


async def test_hand_edited_items_are_locked(store, repo_cfg, repo_dir, tools, ws):
    learner = make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT)
    p = await learner.propose("domains")
    apply(ws, p["id"], ["bills", "bridge"])
    # 사람이 bills 이름을 고치고, 직접 쓴 도메인을 하나 더한다
    raw = yaml.safe_load(ws.file("domains").read_text(encoding="utf-8"))
    raw["domains"][0]["name"] = "법안"
    raw["domains"].append({"id": "manual", "name": "수동", "paths": ["apps/api/**"]})
    raw["domains"][1]["locked"] = True
    ws.file("domains").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    learner.runner.payload = {"domains": [
        {"id": "bills", "name": "의안2", "paths": ["apps/api/**"]},
        {"id": "bridge", "name": "브리지", "paths": ["apps/web/**"]},
    ]}
    p2 = await learner.propose("domains")
    items = {i["key"]: i for i in p2["items"]}
    assert items["bills"]["action"] == "locked" and items["bills"]["locked_reason"] == "손으로 고친 항목"
    assert items["bridge"]["action"] == "locked" and "locked: true" in items["bridge"]["locked_reason"]
    assert "manual" not in items  # 직접 쓴 항목은 초안에 없어도 삭제 제안하지 않는다
    out = apply(ws, p2["id"], ["bills", "bridge"])
    assert out["applied"] == [] and len(out["skipped"]) == 2
    after = yaml.safe_load(ws.file("domains").read_text(encoding="utf-8"))
    assert [d["name"] for d in after["domains"]] == ["법안", "웹뷰 연동", "수동"]


async def test_learned_item_missing_from_draft_is_remove_unchecked(store, repo_cfg, repo_dir, tools, ws):
    learner = make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT)
    apply(ws, (await learner.propose("domains"))["id"], ["bills", "bridge"])
    learner.runner.payload = {"domains": [DOMAIN_DRAFT["domains"][0]]}
    p = await learner.propose("domains")
    items = {i["key"]: i for i in p["items"]}
    assert items["bills"]["action"] == "same"
    assert items["bridge"]["action"] == "remove" and not items["bridge"]["default_accept"]
    apply(ws, p["id"], ["bridge"])
    assert [d.id for d in load_domain_map(ws.config_dir).domains] == ["bills"]


async def test_apply_refuses_when_file_changed(store, repo_cfg, repo_dir, tools, ws):
    p = await make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT).propose("domains")
    ws.file("domains").write_text(DOMAINS_TEMPLATE + "\n# 손으로 고침\n", encoding="utf-8")
    with pytest.raises(LearnError, match="바뀌었어요"):
        apply(ws, p["id"], None)


@pytest.mark.skipif(not paths.rg_path(), reason="rg 필요")
async def test_glossary_identifiers_checked_with_rg(store, repo_cfg, repo_dir, tools, ws):
    payload = {"glossary": [
        {"term": "의안", "identifiers": ["BillsController", "NotInCode"], "evidence": ["apps/api/src/bills/bills.controller.ts"]},
        {"term": "유령", "identifiers": ["Phantom"]},
        {"term": "옛것", "identifiers": ["OLD"]},  # archive/**는 제외 규칙에 걸려 없는 것으로 본다
    ]}
    p = await make_learner(store, repo_cfg, repo_dir, tools, ws, payload).propose("glossary")
    items = {i["key"]: i for i in p["items"]}
    assert items["의안"]["after"] == ["BillsController"]
    assert any("NotInCode" in w for w in items["의안"]["warnings"])
    assert {r["key"] for r in p["rejected"]} == {"유령", "옛것"}
    apply(ws, p["id"], None)
    text = ws.file("glossary").read_text(encoding="utf-8")
    assert text.startswith("# 기획 용어")
    assert load_domain_map(ws.config_dir).glossary == {"의안": ["BillsController"]}


def test_scoping_loader_tolerates_learn_fields(tmp_path: Path):
    (tmp_path / "domains.yaml").write_text("shared:\ndomains:\n  - {id: a, name: A, locked: true, evidence: [x]}\n",
                                           encoding="utf-8")
    (tmp_path / "glossary.yaml").write_text("주문: order\n알림: [push]\n", encoding="utf-8")
    dm = load_domain_map(tmp_path)
    assert dm.get("a").paths == () and dm.shared == ()
    assert dm.glossary == {"주문": ["order"], "알림": ["push"]}


async def test_plan_reports_estimate_without_calling_agent(store, repo_cfg, repo_dir, tools, ws):
    learner = make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT)
    plan = learner.plan("domains")
    assert learner.runner.request is None
    assert plan["ready"] and plan["materials"]["files"] > 0
    assert 0 < plan["estimate_usd"] <= plan["max_usd"] == 2.0
    # 실측 보정: 재료 9천 자·14회 대화에서 실제 $1.09
    assert 0.9 < estimate_usd("claude-sonnet-5-5", 9000, "domains", 14) < 1.3


async def test_pending_proposal_survives_until_applied_or_dismissed(store, repo_cfg, repo_dir, tools, ws):
    learner = make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT)
    assert learner.plan("domains")["pending"] is None
    p1 = await learner.propose("domains")
    p2 = await learner.propose("domains")
    pending = learner.plan("domains")["pending"]
    assert pending["id"] == max(p1["id"], p2["id"]) and "stale" not in pending  # 마지막 제안
    ws.mark_proposal(pending["id"], "dismissed_at")
    assert learner.plan("domains")["pending"] is None  # 버리면 다시 뜨지 않는다

    p3 = await learner.propose("domains")
    store.set_repo_state(repo_cfg.name, "newcommit99")
    assert "동기화" in learner.plan("domains")["pending"]["stale"]
    apply(ws, p3["id"], ["bills"])
    assert ws.load_proposal(p3["id"])["applied_at"]
    assert learner.plan("domains")["pending"] is None  # 적용한 뒤에는 없다


async def test_pending_skipped_when_file_changed(store, repo_cfg, repo_dir, tools, ws):
    learner = make_learner(store, repo_cfg, repo_dir, tools, ws, DOMAIN_DRAFT)
    await learner.propose("domains")
    ws.file("domains").write_text(DOMAINS_TEMPLATE + "\n# 손으로 고침\n", encoding="utf-8")
    assert learner.plan("domains")["pending"] is None
