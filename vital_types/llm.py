from dataclasses import dataclass, field
from typing import Literal


ChatRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class ChatMessage:
    """One message in an LLM conversation."""
    role: ChatRole
    content: str
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolCallRequest:
    """A tool invocation requested by the LLM."""
    id: str
    name: str
    arguments: dict[str, object]


@dataclass
class LlmResponse:
    """Result of one LLM completion (possibly mid tool loop)."""
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    raw_finish_reason: str | None = None


@dataclass
class ToolExecutionResult:
    """Outcome of running one tool against the local database."""
    tool_name: str
    success: bool
    result: dict[str, object]
    error: str | None = None
