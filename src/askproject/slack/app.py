"""슬랙 봇 (Socket Mode). 외부로 열린 포트 없이 슬랙에 바깥 방향으로만 연결한다."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlparse

import httpx
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from askproject.llm.complete import Completer
from askproject.pipeline.answer import AnswerService, evidence_notes
from askproject.pipeline.screenshot import ALLOWED_TYPES, MAX_BYTES, ScreenCapture, extract_screen
from askproject.slack.blocks import answer_blocks, evidence_blocks, fallback_text, mark_followup_picked, thinking_blocks
from askproject.store.db import Store

log = logging.getLogger(__name__)
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
WRAP_CHARS = "`\"'“”‘’"


def clean_question(text: str) -> str:
    """멘션을 지우고, 안내 예시를 복사해 붙인 백틱·따옴표를 걷어낸다."""
    q = MENTION_RE.sub("", text).strip()
    while len(q) >= 2 and q[0] in WRAP_CHARS and q[-1] in WRAP_CHARS:
        q = q[1:-1].strip()
    return q
CLARIFY_RE = re.compile(r"^clarify_pick_")
FOLLOWUP_RE = re.compile(r"^followup_\d+$")
DEFAULT_SCREEN_QUESTION = "이 화면은 어떤 코드로 구현돼 있고, 주요 버튼은 어떤 조건으로 동작해?"


def channel_allowed(allowed: set[str] | None, channel: str) -> bool:
    """답할 채널 허용 목록. 비어 있으면 모두 허용(시작 시 경고). DM(D…)은 'dm'이 있을 때만."""
    if not allowed:
        return True
    return "dm" in allowed if channel.startswith("D") else channel in allowed


def _escape_mrkdwn(text: str) -> str:
    """사용자 글을 mrkdwn에 그대로 넣으면 <!channel>·위장 링크가 살아난다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


NOT_ALLOWED = "이 채널에서는 답하지 않도록 설정돼 있어요. 봇 관리자에게 허용된 채널을 물어봐 주세요."


def build_app(bot_token: str, service: AnswerService, store: Store, escalation_channel: str | None,
              llm: Completer | None = None, allowed_channels: set[str] | None = None) -> AsyncApp:
    app = AsyncApp(token=bot_token)
    inflight = asyncio.Semaphore(4)  # 동시 에이전트 실행 수 제한

    async def read_screenshot(files: list[dict]) -> ScreenCapture | None:
        """첨부 이미지를 메모리에서만 읽어 문구·배치를 뽑는다. 이미지는 저장하지 않는다."""
        img = next((f for f in files or [] if f.get("mimetype") in ALLOWED_TYPES and f.get("size", 0) <= MAX_BYTES), None)
        if not img or not llm:
            return None
        url = img.get("url_private_download") or ""
        u = urlparse(url)
        if img.get("is_external") or u.scheme != "https" or u.hostname != "files.slack.com":
            return None  # 봇 토큰은 슬랙 파일 서버에만 보낸다(외부 파일 URL로 새지 않게)
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(url, headers={"Authorization": f"Bearer {bot_token}"})
            r.raise_for_status()
        return await extract_screen(llm, r.content, img["mimetype"])

    async def deliver(client, channel: str, ts: str, thread_ts: str, answer) -> None:
        """답변 메시지를 갱신하고, 와이어프레임·흐름도가 있으면 HTML 파일을 스레드에 올린다."""
        note = None
        if answer.attachment:
            filename, content = answer.attachment
            what = "흐름도" if answer.attachment_kind == "flow" else "와이어프레임"
            try:
                await client.files_upload_v2(channel=channel, thread_ts=thread_ts, filename=filename, content=content,
                                             title=filename.removesuffix(".html"))
                note = ("🧭 흐름도를 올렸어요. 조건별 갈래와 근거를 이 스레드의 HTML 파일에서 볼 수 있어요."
                        if answer.attachment_kind == "flow"
                        else "🖼 변경안 HTML 파일을 올렸어요. 위에 올린 원본 스크린샷과 나란히 놓고 비교해 보세요."
                        if answer.comparison
                        else "🖼 와이어프레임 HTML 파일을 이 스레드에 올렸어요. 내려받아 브라우저로 열어 보세요.")
            except Exception as exc:
                log.warning("%s upload failed: %s", answer.attachment_kind, exc)
                missing = "missing_scope" in str(exc)
                note = (f"⚠️ 파일 업로드 권한(files:write)이 없어 {what}를 올리지 못했어요. 슬랙 앱을 다시 설치해 주세요."
                        if missing else f"⚠️ {what} 파일을 올리지 못했어요. 잠시 후 다시 시도해 주세요.")
        await client.chat_update(channel=channel, ts=ts, text=fallback_text(answer), blocks=answer_blocks(answer, note=note))

    async def handle_question(client, channel: str, user: str, text: str, thread_ts: str, files: list[dict]) -> None:
        if not channel_allowed(allowed_channels, channel):
            # 허용 목록 밖: PAT 소유자의 레포 읽기 권한이 이 채널 전원에게 열리지 않도록 답하지 않는다
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=NOT_ALLOWED)
            return
        question = clean_question(text)
        has_image = any(f.get("mimetype") in ALLOWED_TYPES for f in files or [])
        if not question and not has_image:
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                          text="무엇이 궁금한지 함께 적어 주세요. 예: `상세 화면은 네이티브야 웹뷰야?`")
            return
        placeholder = await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text="확인 중…",
            blocks=thinking_blocks("⏳ 화면을 읽고 코드를 찾고 있어요…" if has_image else "⏳ 코드를 확인하고 있어요…",
                                   public_note=True))
        try:
            shot = await read_screenshot(files) if has_image else None
            async with inflight:
                answer = await service.answer(question or DEFAULT_SCREEN_QUESTION, source="slack", user_id=user,
                                              channel=channel, thread_ts=thread_ts, screenshot=shot)
            await deliver(client, channel, placeholder["ts"], thread_ts, answer)
        except Exception:
            log.exception("question failed")
            await client.chat_update(channel=channel, ts=placeholder["ts"],
                                     text="처리 중 오류가 났어요. 잠시 후 다시 시도해 주세요.", blocks=[])

    @app.event("app_mention")
    async def on_mention(event, client):
        await handle_question(client, event["channel"], event.get("user", ""), event.get("text", ""),
                              event.get("thread_ts") or event["ts"], event.get("files", []))

    @app.event("message")
    async def on_dm(event, client):
        # DM만 처리. 봇 메시지·수정·삭제 이벤트는 무시한다(봇 자신의 답변을 다시 처리하지 않도록).
        if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
            return
        await handle_question(client, event["channel"], event.get("user", ""), event.get("text", ""),
                              event.get("thread_ts") or event["ts"], event.get("files", []))

    @app.action(CLARIFY_RE)
    async def on_clarify(ack, body, client):
        await ack()
        query_id, _, domain = body["actions"][0]["value"].partition("|")
        if not channel_allowed(allowed_channels, body["channel"]["id"]):
            return
        q = store.execute("SELECT question, user_id, channel, thread_ts FROM queries WHERE id=?", (query_id,)).fetchone()
        if not q:
            return
        channel, ts = body["channel"]["id"], body["message"]["ts"]
        label = body["actions"][0]["text"]["text"]
        await client.chat_update(channel=channel, ts=ts, text="확인 중…",
                                 blocks=thinking_blocks(f"⏳ '{label}' 범위에서 찾고 있어요…"))
        async with inflight:
            answer = await service.answer(q["question"], source="slack", user_id=body["user"]["id"], channel=q["channel"],
                                          thread_ts=q["thread_ts"], forced_domains=[] if domain == "*" else [domain])
        await deliver(client, channel, ts, body["message"].get("thread_ts") or ts, answer)

    async def _thread(body) -> tuple[str, str]:
        msg = body["message"]
        return body["channel"]["id"], msg.get("thread_ts") or msg["ts"]

    @app.action(FOLLOWUP_RE)
    async def on_followup(ack, body, client):
        await ack()
        action = body["actions"][0]
        query_id, _, question = action["value"].partition("|")
        if not question:
            return
        user = body["user"]["id"]
        channel, thread_ts = await _thread(body)
        store.log_feedback(query_id, user, "followup:" + action["action_id"].removeprefix("followup_"))
        msg = body["message"]
        await client.chat_update(channel=channel, ts=msg["ts"], text=msg.get("text") or "답변",
                                 blocks=mark_followup_picked(msg.get("blocks") or [], question))
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"🙋 <@{user}>님의 후속 질문: {question}")
        await handle_question(client, channel, user, question, thread_ts, [])

    @app.action("show_evidence")
    async def on_show_evidence(ack, body, client):
        await ack()
        if not channel_allowed(allowed_channels, body["channel"]["id"]):
            return
        query_id = body["actions"][0]["value"]
        rows = store.execute("SELECT source, path, start_line, end_line, verified, reason FROM citations "
                             "WHERE query_id=? ORDER BY id", (query_id,)).fetchall()
        commit = store.execute("SELECT commit_sha FROM queries WHERE id=?", (query_id,)).fetchone()
        res = store.execute("SELECT route, cost_usd, duration_ms, num_turns, input_tokens, output_tokens, cache_read_tokens, "
                            "cache_write_tokens, answer, provider FROM query_results WHERE query_id=?", (query_id,)).fetchone()
        notes = evidence_notes(res["answer"]) if res else {}
        cites = []
        for r in rows:
            doc = store.doc_file(r["path"]) if r["source"] in ("notion", "slack") else None
            sha = commit["commit_sha"] if commit else "main"
            url = doc["url"] if doc else service.repos.url(r["path"], sha, r["start_line"] or 0)
            cites.append({"source": r["source"], "path": r["path"], "start": r["start_line"], "end": r["end_line"],
                          "verified": bool(r["verified"]), "reason": r["reason"], "url": url,
                          "note": notes.get((r["path"], r["start_line"]), "")})
        tools = store.execute("SELECT tool, input_json FROM tool_calls WHERE query_id=? ORDER BY id", (query_id,)).fetchall()
        steps = []
        for t in tools:
            try:
                steps.append((t["tool"], json.loads(t["input_json"])))
            except ValueError:
                steps.append((t["tool"], {}))
        channel, thread_ts = await _thread(body)
        blocks = evidence_blocks(cites, steps, dict(res) if res else None)
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="근거 전체", blocks=blocks)

    async def _feedback(ack, body, client, value: str, note: str):
        await ack()
        store.log_feedback(body["actions"][0]["value"], body["user"]["id"], value)
        await _notice(body, client, note)

    async def _notice(body, client, note: str):
        # 버튼이 있는 스레드 안에 보여 준다 (thread_ts가 없으면 DM 본 화면으로 가서 반응이 없는 것처럼 보인다)
        msg = body.get("message") or {}
        await client.chat_postEphemeral(channel=body["channel"]["id"], user=body["user"]["id"], text=note,
                                        thread_ts=msg.get("thread_ts") or msg.get("ts"))

    @app.action("feedback_up")
    async def on_up(ack, body, client):
        await _feedback(ack, body, client, "up", "피드백 고마워요 👍")

    @app.action("feedback_down")
    async def on_down(ack, body, client):
        await _feedback(ack, body, client, "down", "피드백이 기록됐어요. 틀린 부분을 스레드에 적어 주시면 개선에 쓰입니다.")

    @app.action("escalate")
    async def on_escalate(ack, body, client):
        query_id = body["actions"][0]["value"]
        already = store.execute("SELECT 1 FROM feedback WHERE query_id=? AND value='escalate' LIMIT 1", (query_id,)).fetchone()
        if already:
            await ack()
            await _notice(body, client, "이 답변은 이미 개발 채널에 확인 요청을 올렸어요.")
            return
        await _feedback(ack, body, client, "escalate", "개발 채널에 확인 요청을 올렸어요." if escalation_channel
                        else "확인 요청 채널이 설정되지 않았어요 — 설정 → 채널에서 운영 채널을 고르세요.")
        if not escalation_channel:
            return
        q = store.execute("SELECT question, channel, thread_ts FROM queries WHERE id=?", (query_id,)).fetchone()
        if not q:
            return
        a = store.execute("SELECT confidence FROM query_results WHERE query_id=?", (query_id,)).fetchone()
        permalink = await client.chat_getPermalink(channel=q["channel"], message_ts=q["thread_ts"])
        await client.chat_postMessage(
            channel=escalation_channel,
            text=(f"<@{body['user']['id']}>님의 확인 요청\n*질문* {_escape_mrkdwn(q['question'])}\n"
                  f"*봇 확신도* {a['confidence'] if a else '-'}\n<{permalink['permalink']}|원래 스레드 보기>"),
        )

    return app


async def serve(app: AsyncApp, app_token: str) -> None:
    handler = AsyncSocketModeHandler(app, app_token)
    log.info("AskProject Slack bot started (Socket Mode)")
    await handler.start_async()
