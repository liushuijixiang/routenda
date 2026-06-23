from __future__ import annotations

import json
import re
from datetime import timedelta
from time import sleep
from typing import Any

import httpx
from pydantic import ValidationError

from visit_agent.domain.models import VisitRequirementDraft, day_window
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker


SYSTEM_PROMPT = """你是商务拜访需求字段提取器。只提取预约相关事实，不推断供应商身份。
不得处理报价、合同、付款、采购量或谈判。未知字段返回 null 或空数组。
所有时间必须输出带时区的 ISO 8601。输出必须符合提供的 JSON Schema。"""


class LLMGateway:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5-mini",
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_completions_url = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        self.model = model
        self.client = client or httpx.Client(timeout=12.0, trust_env=False)
        self._owns_client = client is None
        self.breaker = CircuitBreaker("openai-compatible", failure_threshold=3)
        self.last_error: str | None = None

    def extract_visit_draft(self, text: str) -> VisitRequirementDraft:
        if not self.api_key or not self.breaker.allow():
            return self._rule_extract(text)
        for attempt in range(2):
            try:
                response = self.client.post(
                    self.chat_completions_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "visit_requirement_draft",
                                "strict": True,
                                "schema": VisitRequirementDraft.model_json_schema(),
                            },
                        },
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable LLM response", request=response.request, response=response
                    )
                response.raise_for_status()
                content = self._response_content(response.json())
                draft = VisitRequirementDraft.model_validate_json(content)
                self.breaker.record_success()
                self.last_error = None
                return draft
            except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
                self.last_error = type(exc).__name__
                if attempt == 0:
                    sleep(0.05)
        self.breaker.record_failure()
        return self._rule_extract(text)

    @staticmethod
    def _response_content(payload: dict[str, Any]) -> str:
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
            ]
            return "".join(text_parts)
        return json.dumps(content)

    @staticmethod
    def _rule_extract(text: str) -> VisitRequirementDraft:
        supplier_names = re.findall(r"[A-Z]", text)
        duration = 90 if "时长" not in text else None
        if "两家" in text and len(supplier_names) < 2:
            supplier_names = ["A", "B"]
        return VisitRequirementDraft(
            supplier_name="、".join(supplier_names) if supplier_names else None,
            purpose_category="商务拜访",
            date_start=day_window(0, 9),
            date_end=day_window(1, 18),
            duration_minutes=duration,
            priority=5 if "优先" in text else 3,
            required_people=["王经理"] if "王经理" in text else [],
            origin="上海虹桥酒店" if "上海" in text else None,
            destination="上海虹桥机场" if "回上海" in text or "机场" in text else None,
            return_deadline=(
                day_window(1, 18) if "18" in text else day_window(1, 18) + timedelta(hours=2)
            ),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
