import unittest
import json

import httpx

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.llm_gateway import LLMGateway
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


class AgentGraphTests(unittest.TestCase):
    def test_openai_compatible_gateway_uses_structured_schema_and_pydantic_validation(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers["authorization"]
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "supplier_name": "苏州安科",
                                        "supplier_id": None,
                                        "site_id": None,
                                        "purpose_category": "质量沟通",
                                        "date_start": "2026-06-25T01:00:00Z",
                                        "date_end": "2026-06-25T10:00:00Z",
                                        "duration_minutes": 90,
                                        "priority": 5,
                                        "required_people": ["王经理"],
                                        "origin": "上海虹桥酒店",
                                        "destination": "上海虹桥机场",
                                        "return_deadline": "2026-06-25T10:00:00Z",
                                        "can_move_existing": False,
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        gateway = LLMGateway("secret-key", "https://llm.test/v1", "test-model", client=client)

        draft = gateway.extract_visit_draft("拜访苏州安科")

        self.assertEqual(draft.supplier_name, "苏州安科")
        self.assertEqual(captured["authorization"], "Bearer secret-key")
        payload = captured["payload"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["model"], "test-model")
        client.close()

    def test_gateway_accepts_full_chat_completions_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat/completions")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "supplier_name": "SiliconFlow",
                                        "supplier_id": None,
                                        "site_id": None,
                                        "purpose_category": "商务拜访",
                                        "date_start": None,
                                        "date_end": None,
                                        "duration_minutes": 90,
                                        "priority": 3,
                                        "required_people": [],
                                        "origin": None,
                                        "destination": None,
                                        "return_deadline": None,
                                        "can_move_existing": False,
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        gateway = LLMGateway(
            "secret-key",
            "https://api.siliconflow.test/v1/chat/completions",
            "test-model",
            client=client,
        )

        draft = gateway.extract_visit_draft("拜访")

        self.assertEqual(draft.supplier_name, "SiliconFlow")
        client.close()

    def test_intake_runs_through_compiled_langgraph_and_saves_checkpoint(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)

        result = agent.intake("下周去苏州看 A 供应商，A 优先，王经理最好参加，周四 18 点前回上海。")

        self.assertTrue(result.ok)
        graph_nodes = set(agent.graph.compiled_graph.get_graph().nodes)
        self.assertTrue(
            {
                "receive_input",
                "extract_structured_fields",
                "resolve_supplier_and_people",
                "validate_with_pydantic",
                "compute_missing_slots",
            }.issubset(graph_nodes)
        )
        session_id = result.data["session_id"]
        restored = agent.sessions.get(session_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, session_id)
        self.assertEqual(restored.missing_slots, result.data["missing_slots"])

    def test_context_builder_scopes_data_to_one_requirement(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)
        requirement_ids = list(repo.requirements)

        context = agent.context.for_requirement(requirement_ids[0])

        self.assertEqual(context["requirement"].id, requirement_ids[0])
        self.assertTrue(
            all(item.requirement_id == requirement_ids[0] for item in context["availability"])
        )
        self.assertNotIn("suppliers", context)
        self.assertNotIn("requirements", context)


if __name__ == "__main__":
    unittest.main()
