from __future__ import annotations


class AgentError(Exception):
    """Base error for agent framework failures."""


class LLMError(AgentError):
    """Raised when an LLM provider cannot return a usable response."""


class ToolError(AgentError):
    """Raised when a tool cannot be executed."""


class ToolNotFoundError(ToolError):
    """Raised when an agent requests an unknown tool."""
