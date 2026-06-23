from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from visit_agent.agent.runtime import AgentRuntime
from visit_agent.config import settings
from visit_agent.core.message import Message
from visit_agent.infrastructure.adapters.feishu import FeishuOpenPlatformAdapter


logger = logging.getLogger(__name__)


class FeishuMessageParseError(ValueError):
    pass


@dataclass(frozen=True)
class FeishuMessage:
    message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    sender_type: str
    message_type: str
    text: str


def _attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _sender_id(sender: Any) -> str:
    sender_id = _attr(sender, "sender_id")
    return str(
        _attr(sender_id, "open_id")
        or _attr(sender_id, "user_id")
        or _attr(sender_id, "union_id")
        or ""
    )


def _text_content(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    if not isinstance(content, str):
        return ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content.strip()
    if isinstance(parsed, dict):
        return str(parsed.get("text", "")).strip()
    return content.strip()


def _clean_group_mentions(text: str, mentions: Any) -> str:
    cleaned = text
    for mention in mentions or []:
        key = _attr(mention, "key")
        name = _attr(mention, "name")
        if key:
            cleaned = cleaned.replace(str(key), " ")
        if name:
            cleaned = cleaned.replace(f"@{name}", " ")
    cleaned = re.sub(r"@_user_\d+", " ", cleaned)
    return " ".join(cleaned.split())


def parse_feishu_message(event: Any) -> FeishuMessage:
    body = _attr(event, "event")
    message = _attr(body, "message")
    sender = _attr(body, "sender")
    fields = {
        "message_id": _attr(message, "message_id"),
        "chat_id": _attr(message, "chat_id"),
        "chat_type": _attr(message, "chat_type"),
        "sender_id": _sender_id(sender),
        "message_type": _attr(message, "message_type"),
    }
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise FeishuMessageParseError("事件缺少字段: " + ", ".join(missing))

    text = ""
    if fields["message_type"] == "text":
        text = _text_content(_attr(message, "content"))
        if fields["chat_type"] == "group":
            text = _clean_group_mentions(text, _attr(message, "mentions"))

    return FeishuMessage(
        message_id=str(fields["message_id"]),
        chat_id=str(fields["chat_id"]),
        chat_type=str(fields["chat_type"]),
        sender_id=str(fields["sender_id"]),
        sender_type=str(_attr(sender, "sender_type") or ""),
        message_type=str(fields["message_type"]),
        text=text.strip(),
    )


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class FeishuEventQueue:
    """Keeps Feishu SDK callbacks small and runs agent work in worker threads."""

    def __init__(
        self,
        handler: Callable[[Any], None],
        *,
        worker_count: int = 2,
        max_size: int = 1000,
    ) -> None:
        self.handler = handler
        self.queue: queue.Queue[Any | None] = queue.Queue(maxsize=max_size)
        self.workers: list[threading.Thread] = []
        self.worker_count = worker_count
        self._accepting = True

    def start(self) -> None:
        for index in range(self.worker_count):
            worker = threading.Thread(
                target=self._worker,
                name=f"feishu-event-worker-{index + 1}",
                daemon=False,
            )
            worker.start()
            self.workers.append(worker)

    def submit(self, event: Any) -> None:
        if not self._accepting:
            logger.warning("ignore_feishu_event_while_stopping")
            return
        self.queue.put_nowait(event)

    def shutdown(self) -> None:
        self._accepting = False
        self.queue.join()
        for _ in self.workers:
            self.queue.put(None)
        for worker in self.workers:
            worker.join()

    def _worker(self) -> None:
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    return
                self.handler(event)
            except Exception:
                logger.exception("feishu_event_worker_failed")
            finally:
                self.queue.task_done()


class FeishuAgentEventHandler:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        send_text: Callable[[str, str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.send_text = send_text or self._send_text
        self._seen_message_ids: set[str] = set()
        self._histories: dict[str, list[Message]] = {}
        self._lock = threading.Lock()

    def handle(self, event: Any) -> None:
        message = parse_feishu_message(event)
        logger.info(
            "received_feishu_message message_id=%s chat_id=%s chat_type=%s sender_id=%s "
            "sender_type=%s message_type=%s text=%s",
            message.message_id,
            message.chat_id,
            message.chat_type,
            message.sender_id,
            message.sender_type,
            message.message_type,
            message.text,
        )
        if message.sender_type in {"app", "bot"}:
            logger.info("ignore_bot_message message_id=%s", message.message_id)
            return
        if not self._claim(message.message_id):
            logger.info("ignore_duplicate_message message_id=%s", message.message_id)
            return
        if message.message_type != "text":
            self.send_text(message.chat_id, "当前版本暂时只支持文本消息。")
            return
        history = self._conversation(message.chat_id, message.text)
        turn = run_async(self.runtime.run_messages(history))
        self._remember(message.chat_id, message.text, turn.reply)
        self.runtime.remember_interaction(message.chat_id, message.text, turn.reply)
        logger.info(
            "agent_reply_ready message_id=%s tool_calls=%s reply_preview=%s",
            message.message_id,
            [call.name for call in turn.tool_calls],
            turn.reply[:120].replace("\n", " "),
        )
        self.send_text(message.chat_id, turn.reply)

    def _claim(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._seen_message_ids:
                return False
            self._seen_message_ids.add(message_id)
            return True

    def _conversation(self, chat_id: str, text: str) -> list[Message]:
        with self._lock:
            history = list(self._histories.get(chat_id, []))
        return [*history[-10:], Message("user", text)]

    def _remember(self, chat_id: str, text: str, reply: str) -> None:
        with self._lock:
            history = self._histories.setdefault(chat_id, [])
            history.extend([Message("user", text), Message("assistant", reply)])
            del history[:-12]

    @staticmethod
    def _send_text(chat_id: str, text: str) -> None:
        async def send() -> None:
            feishu = FeishuOpenPlatformAdapter(
                settings.feishu_app_id,
                settings.feishu_app_secret,
                base_url=settings.feishu_base_url,
            )
            try:
                result = await feishu.send_text(chat_id, text, receive_id_type="chat_id")
                if not result.ok:
                    raise RuntimeError(result.message or result.error_code or "send failed")
            finally:
                await feishu.aclose()

        run_async(send())
