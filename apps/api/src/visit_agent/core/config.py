from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    name: str = "Routenda Agent"
    system_prompt: str = (
        "你是 Routenda 商务拜访智能体。你能先自然对话，再按需要调用工具处理"
        "拜访需求、供应商、日历、搜索和行程规划。回答要简洁、可执行。"
    )
    max_iterations: int = 4
    temperature: float = 0.2
    max_tokens: int = 1200
