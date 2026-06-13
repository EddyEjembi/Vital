"""Helpers for coach chat — write-intent detection and tool-result checks."""

import json
import re

from openai.types.chat import ChatCompletionMessageParam

from vital_types.llm import ChatMessage

_FOOD_LOG_WORDS = (
    "lunch",
    "breakfast",
    "dinner",
    "snack",
    "meal",
    "ate",
    "eaten",
    "food",
    "beans",
    "plantain",
    "rice",
    "chicken",
)
_ATE_PHRASES = (
    "i just had",
    "i ate",
    "i had ",
    "just ate",
    "had for lunch",
    "had for breakfast",
    "had for dinner",
)
_LOG_WORDS = ("log", "record", "track", "save", "note")


def recent_user_text(
    user_message: str,
    extra_messages: list[ChatMessage] | None,
    max_turns: int = 4,
) -> str:
    """Combine the current message with recent user turns for context."""
    parts: list[str] = []
    if extra_messages:
        for message in reversed(extra_messages):
            if message.role != "user":
                continue
            stripped = message.content.strip()
            if stripped:
                parts.insert(0, stripped)
            if len(parts) >= max_turns - 1:
                break
    parts.append(user_message.strip())
    return " ".join(part for part in parts if part)


def user_requests_food_log(
    user_message: str,
    extra_messages: list[ChatMessage] | None,
) -> bool:
    """Return true when the user wants a meal written to food_log."""
    current = user_message.strip().lower()
    combined = recent_user_text(user_message, extra_messages).lower()

    has_food_context = any(word in combined for word in _FOOD_LOG_WORDS)
    has_food_context = has_food_context or any(phrase in combined for phrase in _ATE_PHRASES)

    if not has_food_context:
        return False

    if any(word in current for word in _LOG_WORDS):
        return True

    if any(phrase in combined for phrase in _ATE_PHRASES):
        return True

    # Follow-up: "log it for me" after an earlier meal description.
    if ("log" in current or "record" in current) and (
        "it" in current or "that" in current or "lunch" in current
    ):
        return True

    return False


def turn_had_successful_tool(
    api_messages: list[ChatCompletionMessageParam],
    tool_name: str,
) -> bool:
    """Return true if a tool returned success:true during this chat turn."""
    for message in api_messages:
        if message.get("role") != "tool":
            continue
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            continue
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError:
            continue
        if payload.get("tool") == tool_name and payload.get("success") is True:
            return True
    return False


def infer_meal_type_from_text(text: str) -> str:
    """Guess meal_type from user wording when the model omits it."""
    lowered = text.lower()
    if "breakfast" in lowered:
        return "breakfast"
    if "dinner" in lowered:
        return "dinner"
    if "snack" in lowered:
        return "snack"
    return "lunch"


def extract_food_description(text: str) -> str | None:
    """Pull a simple food description from natural language."""
    patterns = [
        re.compile(r"i (?:just )?ate (.+?)(?:\.|,|!|\?|$)", re.IGNORECASE),
        re.compile(r"i (?:just )?had (.+?)(?:\.|,|!|\?|$)", re.IGNORECASE),
        re.compile(r"(?:for )?lunch[:\s]+(.+?)(?:\.|,|!|\?|$)", re.IGNORECASE),
        re.compile(r"(?:beans|plantain|rice|chicken|fish)[^.!?]*", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip() if match.lastindex else match.group(0).strip()
            if candidate and len(candidate) > 2:
                cleaned = re.sub(
                    r"\b(?:can you log(?: that| it)?(?: for me)?|please log|log it)\b",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                ).strip(" .,!")
                if cleaned:
                    return cleaned
    return None
