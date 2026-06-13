"""LLM runtime configuration — Modal vLLM OpenAI-compatible endpoint."""

import os
from dataclasses import dataclass

from core.app_config import PROJECT_ROOT

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

DEFAULT_MODEL_ID = "nemotron3-nano-4B-BF16"
DEFAULT_BASE_URL = "http://localhost:8000/v1"


@dataclass(frozen=True)
class LlmConfig:
    """Resolved LLM client configuration."""
    base_url: str
    model_id: str
    api_key: str
    max_tokens: int
    context_limit_tokens: int
    tool_temperature: float
    json_temperature: float
    max_tool_iterations: int
    request_timeout_seconds: float
    daily_schedule_max_attempts: int
    daily_schedule_retry_delay_seconds: float
    daily_schedule_max_tokens: int


def get_llm_config() -> LlmConfig:
    """Load LLM settings from environment variables."""
    return LlmConfig(
        base_url=os.getenv("VITAL_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model_id=os.getenv("VITAL_MODEL_ID", DEFAULT_MODEL_ID),
        api_key=os.getenv("VITAL_LLM_API_KEY", "vital-local"),
        max_tokens=int(os.getenv("VITAL_MAX_TOKENS", "4096")),
        context_limit_tokens=int(os.getenv("VITAL_CONTEXT_LIMIT", "8192")),
        tool_temperature=float(os.getenv("VITAL_TOOL_TEMPERATURE", "0.6")),
        json_temperature=float(os.getenv("VITAL_JSON_TEMPERATURE", "0.4")),
        max_tool_iterations=int(os.getenv("VITAL_MAX_TOOL_ITERATIONS", "5")),
        request_timeout_seconds=float(os.getenv("VITAL_LLM_TIMEOUT_SECONDS", "900")),
        daily_schedule_max_attempts=int(os.getenv("VITAL_DAILY_SCHEDULE_ATTEMPTS", "3")),
        daily_schedule_retry_delay_seconds=float(
            os.getenv("VITAL_DAILY_SCHEDULE_RETRY_DELAY", "30")
        ),
        daily_schedule_max_tokens=int(os.getenv("VITAL_DAILY_SCHEDULE_MAX_TOKENS", "4096")),
    )
