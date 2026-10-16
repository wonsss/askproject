"""워크스페이스 호스트 1대 보장. Socket Mode는 같은 앱 토큰의 연결들에 이벤트를 나눠 보내므로,
봇이 두 대 켜지면 질문의 일부가 다른 맥(다른 설정·레포)에서 답해진다.

각 호스트는 운영 채널(host_channel, 없으면 escalation_channel)에 메시지 하나를 두고 2분마다 제자리 갱신한다.
메시지 metadata(event_type=askproject_host_heartbeat)에 호스트 ID·이름·갱신 시각을 싣는다.
시작할 때 다른 호스트의 최근 하트비트가 보이면 연결하지 않고 기다린다(상태는 data/host_conflict.json → 앱 경고).
"""

from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from pathlib import Path

from askproject import paths

log = logging.getLogger(__name__)

EVENT_TYPE = "askproject_host_heartbeat"
INTERVAL_SEC = 120
STALE_SEC = 330  # 하트비트 두 번 반 넘게 소식이 없으면 꺼진 호스트로 본다
HISTORY_LIMIT = 200


def host_identity(data_dir: Path) -> tuple[str, str]:
    """(호스트 ID, 표시 이름). ID는 데이터 폴더마다 한 번 만들어 둔다."""
    f = data_dir / "host_id"
    try:
        host_id = f.read_text().strip()
    except OSError:
        host_id = ""
    if not host_id:
        host_id = uuid.uuid4().hex
        paths.atomic_write(f, host_id, mode=0o644)
    return host_id, socket.gethostname().removesuffix(".local")


def conflict_path(data_dir: Path) -> Path:
    return data_dir / "host_conflict.json"


def read_conflict(data_dir: Path) -> dict | None:
    try:
        return json.loads(conflict_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Heartbeat:
    def __init__(self, client, channel: str, data_dir: Path):
        self.client = client
        self.channel = channel
        self.data_dir = data_dir
        self.host_id, self.host_name = host_identity(data_dir)
        self._state_file = data_dir / "host_heartbeat.json"
        self.ts = self._saved_ts()

    def _saved_ts(self) -> str | None:
        try:
            saved = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return saved.get("ts") if saved.get("channel") == self.channel else None

    def _payload(self, state: str) -> dict:
        return {"event_type": EVENT_TYPE,
                "event_payload": {"host_id": self.host_id, "host_name": self.host_name,
                                  "state": state, "updated_at": int(time.time())}}

    async def others(self) -> list[dict]:
        """최근 하트비트를 보낸 다른 호스트들."""
        r = await self.client.conversations_history(channel=self.channel, limit=HISTORY_LIMIT, include_all_metadata=True)
        now, found = time.time(), {}
        for m in r.get("messages", []):
            meta = m.get("metadata") or {}
            p = meta.get("event_payload") or {}
            if meta.get("event_type") != EVENT_TYPE or p.get("host_id") in (None, self.host_id):
                continue
            if p.get("state") == "running" and now - float(p.get("updated_at", 0)) < STALE_SEC:
                found[p["host_id"]] = {"host_name": p.get("host_name", "?"), "updated_at": p.get("updated_at")}
        return list(found.values())

    async def beat(self, state: str = "running") -> None:
        text = (f"🖥 AskProject 호스트 *{self.host_name}* · {'실행 중' if state == 'running' else '꺼짐'} · "
                f"{time.strftime('%m-%d %H:%M')} 확인\n_봇이 두 대 켜지는 것을 막는 자동 메시지예요. 지우지 마세요._")
        meta = self._payload(state)
        if self.ts:
            try:
                await self.client.chat_update(channel=self.channel, ts=self.ts, text=text, metadata=meta)
                return
            except Exception as exc:  # 메시지가 지워졌으면 새로 올린다
                log.info("heartbeat update failed (%s), posting new", exc)
        r = await self.client.chat_postMessage(channel=self.channel, text=text, metadata=meta)
        self.ts = r["ts"]
        paths.atomic_write(self._state_file, json.dumps({"channel": self.channel, "ts": self.ts}), mode=0o644)

    def record(self, others: list[dict]) -> None:
        f = conflict_path(self.data_dir)
        if others:
            prev = read_conflict(self.data_dir) or {}
            paths.atomic_write(f, json.dumps({"others": others, "since": prev.get("since") or int(time.time())},
                                             ensure_ascii=False), mode=0o644)
        else:
            f.unlink(missing_ok=True)

    async def wait_until_alone(self, sleep=None) -> None:
        """다른 호스트가 켜져 있는 동안 기다린다. 그쪽이 꺼지면(하트비트가 끊기면) 이어서 시작한다."""
        import asyncio

        sleep = sleep or asyncio.sleep
        while True:
            others = await self.others()
            self.record(others)
            if not others:
                return
            names = ", ".join(o["host_name"] for o in others)
            log.warning("다른 AskProject 호스트(%s)가 같은 슬랙 앱으로 실행 중이에요 — 끝날 때까지 기다립니다", names)
            await sleep(60)

    async def run(self, sleep=None) -> None:
        """실행 중 하트비트. 그사이 다른 호스트가 나타나면 경고만 기록한다(연결은 유지)."""
        import asyncio

        sleep = sleep or asyncio.sleep
        while True:
            try:
                await self.beat()
                others = await self.others()
                self.record(others)
                if others:
                    log.warning("다른 AskProject 호스트가 함께 실행 중이에요: %s", ", ".join(o["host_name"] for o in others))
            except Exception:
                log.exception("heartbeat failed")
            await sleep(INTERVAL_SEC)
