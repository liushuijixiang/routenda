import asyncio
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.runtime import AgentRuntime
from visit_agent.agents.react_agent import ReactAgent
from visit_agent.agents.simple_agent import SimpleAgent
from visit_agent.api.feishu_events import (
    FeishuAgentEventHandler,
    FeishuEventQueue,
    parse_feishu_message,
)
from visit_agent.core.llm import FallbackLLM
from visit_agent.tools.builtin.calculator import CalculatorTool
from visit_agent.tools.builtin.agentic_rl import AgenticRLTool
from visit_agent.tools.builtin.communication import CommunicationProtocolTool
from visit_agent.tools.builtin.context_engineering import ContextEngineeringTool
from visit_agent.tools.builtin.memory import MemoryTool
from visit_agent.tools.builtin.rag import RAGTool
from visit_agent.tools.builtin.storage import StorageTool
from visit_agent.tools.registry import ToolRegistry
from visit_agent.tools.store import AgentSQLiteStore
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


class AgentRuntimeTests(unittest.TestCase):
    def test_visit_message_calls_requirement_supplier_and_plan_tools(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )

        turn = asyncio.run(
            runtime.run("下周去苏州安科做质量沟通，王经理参加，周四前回上海，帮我规划行程")
        )

        tool_names = [call.name for call in turn.tool_calls]
        self.assertIn("extract_visit_requirement", tool_names)
        self.assertIn("search_suppliers", tool_names)
        self.assertIn("generate_itinerary_plan", tool_names)
        self.assertIn("search_suppliers", turn.reply)
        self.assertIn("苏州安科", turn.reply)

    def test_calendar_message_calls_calendar_tool(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )

        turn = asyncio.run(runtime.run("看看今天日历和日程"))

        tool_names = [call.name for call in turn.tool_calls]
        self.assertEqual(tool_names, ["extract_visit_requirement", "feishu_calendar"])
        self.assertIn("飞书凭据未配置", turn.reply)

    def test_short_chat_has_non_template_response(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )

        turn = asyncio.run(runtime.run("你好"))

        self.assertIn("我在", turn.reply)
        self.assertNotIn("你刚才说", turn.reply)

    def test_simple_agent_handles_plain_chat(self) -> None:
        agent = SimpleAgent(FallbackLLM())

        response = asyncio.run(agent.run("你好"))

        self.assertIn("我在", response.content)
        self.assertEqual(response.tool_calls, [])

    def test_react_agent_can_call_builtin_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        agent = ReactAgent(FallbackLLM(), registry)

        response = asyncio.run(agent.run("1 + 2 * 3"))

        self.assertEqual([call.name for call in response.tool_calls], ["calculator"])
        self.assertIn("calculator", response.content)
        self.assertIn("7", response.content)

    def test_advanced_tools_are_installed_on_runtime(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )

        names = {tool.name for tool in runtime.tools.list()}

        self.assertTrue(
            {
                "memory",
                "rag",
                "storage",
                "context_engineering",
                "communication_protocol",
                "agentic_rl",
            }.issubset(names)
        )

    def test_advanced_tools_persist_and_retrieve_context(self) -> None:
        with TemporaryDirectory() as tmp:
            store = AgentSQLiteStore(f"{tmp}/agent.sqlite3")
            storage = StorageTool(store)
            memory = MemoryTool(store)
            rag = RAGTool(store)
            context = ContextEngineeringTool(store)
            protocol = CommunicationProtocolTool()
            rl = AgenticRLTool(store)

            self.assertTrue(
                storage.run(
                    {"operation": "set", "namespace": "test", "key": "model", "value": "deepseek"}
                ).ok
            )
            self.assertIn("deepseek", storage.run({"namespace": "test", "key": "model"}).output)
            self.assertTrue(memory.run({"operation": "remember", "text": "用户偏好上午拜访"}).ok)
            self.assertTrue(rag.run({"operation": "ingest", "text": "青岛桃花源酒店适合商务会面"}).ok)
            self.assertIn("上午拜访", memory.run({"query": "上午拜访"}).output)
            self.assertIn("青岛桃花源", rag.run({"query": "青岛酒店"}).output)
            self.assertIn("当前任务", context.run({"query": "安排上午拜访青岛酒店"}).output)
            self.assertTrue(protocol.run({"protocol": "mcp", "payload": {"task": "calendar"}}).ok)
            self.assertTrue(rl.run({"operation": "record", "task": "chat", "signal": 1}).ok)
            self.assertIn("平均信号", rl.run({"operation": "summary"}).output)


class FeishuAgentEventTests(unittest.TestCase):
    def test_parse_feishu_text_message(self) -> None:
        event = _feishu_event("msg-1", "chat-1", "hello")

        message = parse_feishu_message(event)

        self.assertEqual(message.message_id, "msg-1")
        self.assertEqual(message.chat_id, "chat-1")
        self.assertEqual(message.message_type, "text")
        self.assertEqual(message.text, "hello")

    def test_handler_filters_duplicates_and_bot_messages(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )
        sent: list[tuple[str, str]] = []
        handler = FeishuAgentEventHandler(runtime, send_text=lambda chat_id, text: sent.append((chat_id, text)))

        handler.handle(_feishu_event("msg-1", "chat-1", "日历"))
        handler.handle(_feishu_event("msg-1", "chat-1", "日历"))
        handler.handle(_feishu_event("msg-2", "chat-1", "日历", sender_type="bot"))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "chat-1")
        self.assertIn("feishu_calendar", sent[0][1])

    def test_handler_keeps_conversation_history(self) -> None:
        runtime = AgentRuntime(
            VisitCoordinatorAgent(seed_demo(InMemoryRepository())),
            llm=FallbackLLM(),
        )
        sent: list[tuple[str, str]] = []
        handler = FeishuAgentEventHandler(runtime, send_text=lambda chat_id, text: sent.append((chat_id, text)))

        handler.handle(_feishu_event("msg-1", "chat-1", "你好"))
        handler.handle(_feishu_event("msg-2", "chat-1", "继续"))

        self.assertEqual(len(sent), 2)
        self.assertEqual(len(handler._histories["chat-1"]), 4)

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
