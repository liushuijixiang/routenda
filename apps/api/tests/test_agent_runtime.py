import asyncio
from types import SimpleNamespace
import unittest

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.runtime import AgentRuntime
from visit_agent.api.feishu_events import (
    FeishuAgentEventHandler,
    FeishuEventQueue,
    parse_feishu_message,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


class AgentRuntimeTests(unittest.TestCase):
    def test_visit_message_calls_requirement_supplier_and_plan_tools(self) -> None:
        runtime = AgentRuntime(VisitCoordinatorAgent(seed_demo(InMemoryRepository())))

        turn = asyncio.run(
            runtime.run("下周去苏州安科做质量沟通，王经理参加，周四前回上海，帮我规划行程")
        )

        tool_names = [call.name for call in turn.tool_calls]
        self.assertIn("extract_visit_requirement", tool_names)
        self.assertIn("search_suppliers", tool_names)
        self.assertIn("generate_itinerary_plan", tool_names)
        self.assertIn("Routenda Agent", turn.reply)
        self.assertIn("search_suppliers", turn.reply)

    def test_calendar_message_calls_calendar_tool(self) -> None:
        runtime = AgentRuntime(VisitCoordinatorAgent(seed_demo(InMemoryRepository())))

        turn = asyncio.run(runtime.run("看看今天日历和日程"))

        tool_names = [call.name for call in turn.tool_calls]
        self.assertEqual(tool_names, ["extract_visit_requirement", "feishu_calendar"])
        self.assertIn("飞书凭据未配置", turn.reply)


class FeishuAgentEventTests(unittest.TestCase):
    def test_parse_feishu_text_message(self) -> None:
        event = _feishu_event("msg-1", "chat-1", "hello")

        message = parse_feishu_message(event)

        self.assertEqual(message.message_id, "msg-1")
        self.assertEqual(message.chat_id, "chat-1")
        self.assertEqual(message.message_type, "text")
        self.assertEqual(message.text, "hello")

    def test_handler_filters_duplicates_and_bot_messages(self) -> None:
        runtime = AgentRuntime(VisitCoordinatorAgent(seed_demo(InMemoryRepository())))
        sent: list[tuple[str, str]] = []
        handler = FeishuAgentEventHandler(runtime, send_text=lambda chat_id, text: sent.append((chat_id, text)))

        handler.handle(_feishu_event("msg-1", "chat-1", "日历"))
        handler.handle(_feishu_event("msg-1", "chat-1", "日历"))
        handler.handle(_feishu_event("msg-2", "chat-1", "日历", sender_type="bot"))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "chat-1")
        self.assertIn("feishu_calendar", sent[0][1])

    def test_event_queue_runs_submitted_events(self) -> None:
        seen: list[str] = []
        events = FeishuEventQueue(lambda event: seen.append(event), worker_count=1)
        events.start()

        events.submit("one")
        events.shutdown()

        self.assertEqual(seen, ["one"])


def _feishu_event(
    message_id: str,
    chat_id: str,
    text: str,
    *,
    sender_type: str = "user",
) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=message_id,
                chat_id=chat_id,
                chat_type="p2p",
                message_type="text",
                content=f'{{"text": "{text}"}}',
            ),
            sender=SimpleNamespace(
                sender_type=sender_type,
                sender_id=SimpleNamespace(open_id="ou-test"),
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
