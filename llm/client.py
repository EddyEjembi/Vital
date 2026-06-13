"""OpenAI-compatible LLM client for Modal vLLM with local tool execution."""

import json
import logging
import re
import time
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from vital_types.llm import ChatMessage, LlmResponse, ToolCallRequest

from llm.chat_helpers import (
    extract_food_description,
    infer_meal_type_from_text,
    turn_had_successful_tool,
    user_requests_food_log,
    recent_user_text,
)
from llm.config import LlmConfig, get_llm_config
from llm.system_prompt import build_system_prompt, trim_prompt_to_budget
from llm.tool_runner import execute_tool, tool_result_to_json
from llm.tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

_JSON_BLOCK_PATTERN = re.compile(r"\{[\s\S]*\}")


def _json_schema_response_format(schema: dict[str, object]) -> dict[str, object]:
    """Build a schema-constrained response_format for vLLM structured outputs."""
    return {
        "type": "json_schema",
        "json_schema": {"name": "structured_output", "schema": schema},
    }
_PROMPT_TOOL_PATTERN = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*(\{[\s\S]*?\})\s*\}'
)

# Schema for the constrained retry when a free-form coach reply fails
# validation. Tool calls and constrained decoding cannot be combined in one
# request, so the tool loop runs free and this schema guards the final answer.
COACH_REPLY_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "The coach's conversational answer to the user, plain text.",
        },
    },
    "required": ["reply"],
    "additionalProperties": False,
}

_MAX_CHAT_REPLY_CHARS = 4000


def _validate_chat_reply(content: str) -> str | None:
    """Return a cleaned coach reply, or None when the text fails validation.

    Rejects empty output and replies that are leaked JSON (tool-call spill or
    a raw object instead of prose), which Nemotron occasionally emits.
    """
    trimmed = content.strip()
    if not trimmed:
        return None
    if trimmed.startswith("{") and trimmed.endswith("}"):
        return None
    if _PROMPT_TOOL_PATTERN.search(trimmed):
        return None
    if len(trimmed) > _MAX_CHAT_REPLY_CHARS:
        trimmed = trimmed[:_MAX_CHAT_REPLY_CHARS].rstrip() + "..."
    return trimmed


class LlmClient:
    """Calls the remote Nemotron endpoint and runs tools locally."""

    def __init__(self, config: LlmConfig | None = None):
        self.config = config or get_llm_config()
        self._openai = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.request_timeout_seconds,
        )

    def _to_api_messages(self, messages: list[ChatMessage]) -> list[ChatCompletionMessageParam]:
        """Convert internal messages to OpenAI API format."""
        api_messages: list[ChatCompletionMessageParam] = []
        for message in messages:
            if message.role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": message.tool_call_id or "",
                    }
                )
                continue
            api_messages.append(
                {
                    "role": message.role,
                    "content": message.content,
                }
            )
        return api_messages

    def _parse_tool_arguments(self, raw_arguments: str) -> dict[str, object]:
        """Parse tool call arguments JSON from the model."""
        if not raw_arguments.strip():
            return {}
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object.")
        return cast(dict[str, object], parsed)

    def _extract_prompt_based_tool_call(self, content: str) -> ToolCallRequest | None:
        """Fallback: parse {"tool": "...", "args": {...}} from plain text."""
        match = _PROMPT_TOOL_PATTERN.search(content)
        if match is None:
            return None
        tool_name = match.group(1)
        args_json = match.group(2)
        arguments = self._parse_tool_arguments(args_json)
        return ToolCallRequest(id="prompt_tool_0", name=tool_name, arguments=arguments)

    def _completion_to_response(
        self,
        message_content: str | None,
        message,
        finish_reason: str | None = None,
    ) -> LlmResponse:
        """Map an OpenAI completion message to an internal LlmResponse."""
        tool_calls: list[ToolCallRequest] = []
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            for index, tool_call in enumerate(raw_tool_calls):
                function_block = tool_call.function
                arguments = self._parse_tool_arguments(function_block.arguments or "{}")
                tool_calls.append(
                    ToolCallRequest(
                        id=tool_call.id or f"tool_{index}",
                        name=function_block.name,
                        arguments=arguments,
                    )
                )

        content = message_content or ""
        if not tool_calls:
            fallback = self._extract_prompt_based_tool_call(content)
            if fallback is not None:
                tool_calls = [fallback]
                content = ""

        resolved_finish_reason = finish_reason or getattr(message, "finish_reason", None)
        return LlmResponse(
            content=content,
            tool_calls=tool_calls,
            raw_finish_reason=resolved_finish_reason,
        )

    def complete(
        self,
        messages: list[ChatMessage],
        use_tools: bool = True,
        json_mode: bool = False,
        temperature: float | None = None,
        json_schema: dict[str, object] | None = None,
    ) -> LlmResponse:
        """Send one chat completion request to the remote LLM."""
        api_messages = self._to_api_messages(messages)
        request_temperature = temperature
        if request_temperature is None:
            request_temperature = (
                self.config.json_temperature if json_mode else self.config.tool_temperature
            )

        kwargs: dict[str, object] = {
            "model": self.config.model_id,
            "messages": api_messages,
            "temperature": request_temperature,
            "max_tokens": self.config.max_tokens,
        }
        if use_tools:
            kwargs["tools"] = TOOL_SCHEMAS
            kwargs["tool_choice"] = "auto"
        if json_schema is not None:
            kwargs["response_format"] = _json_schema_response_format(json_schema)
        elif json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        completion = self._openai.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        return self._completion_to_response(
            message.content,
            message,
            finish_reason=choice.finish_reason,
        )

    def _run_completion_on_api_messages(
        self,
        api_messages: list[ChatCompletionMessageParam],
        use_tools: bool,
        json_mode: bool = False,
        temperature: float | None = None,
        tools_override: list[dict[str, object]] | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, object] | None = None,
    ) -> LlmResponse:
        """Send a completion using raw OpenAI API message dicts."""
        request_temperature = temperature
        if request_temperature is None:
            request_temperature = (
                self.config.json_temperature if json_mode else self.config.tool_temperature
            )

        kwargs: dict[str, object] = {
            "model": self.config.model_id,
            "messages": api_messages,
            "temperature": request_temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        if use_tools:
            kwargs["tools"] = tools_override if tools_override is not None else TOOL_SCHEMAS
            kwargs["tool_choice"] = "auto"
        if json_schema is not None:
            kwargs["response_format"] = _json_schema_response_format(json_schema)
        elif json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        completion = self._openai.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        return self._completion_to_response(
            message.content,
            message,
            finish_reason=choice.finish_reason,
        )

    def chat(
        self,
        user_message: str,
        extra_messages: list[ChatMessage] | None = None,
        use_tools: bool = True,
    ) -> str:
        """Run a full coach conversation turn with optional tool loop."""
        system_prompt = trim_prompt_to_budget(
            build_system_prompt(include_tools_instruction=use_tools),
            self.config.context_limit_tokens,
        )
        api_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
        ]
        if extra_messages:
            api_messages.extend(self._to_api_messages(extra_messages))
        api_messages.append({"role": "user", "content": user_message})
        food_log_requested = user_requests_food_log(user_message, extra_messages)

        for _iteration in range(self.config.max_tool_iterations):
            response = self._run_completion_on_api_messages(api_messages, use_tools=use_tools)
            if not response.tool_calls:
                if food_log_requested and not turn_had_successful_tool(api_messages, "log_food"):
                    logger.warning(
                        "[chat] User requested food log but model replied without log_food — "
                        "nudging for write tool.",
                    )
                    api_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Before answering, call log_food now with meal_type and "
                                "food_description from what I said I ate. Do not claim you "
                                "logged anything until log_food returns success:true."
                            ),
                        }
                    )
                    continue

                validated_reply = _validate_chat_reply(response.content)
                if validated_reply is not None:
                    return validated_reply
                logger.warning(
                    "[chat] Free-form reply failed validation (%s chars) — "
                    "retrying with strict reply schema.",
                    len(response.content),
                )
                break

            assistant_tool_calls: list[dict[str, object]] = []
            for tool_call in response.tool_calls:
                assistant_tool_calls.append(
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments),
                        },
                    }
                )

            # Drop assistant text when tool calls are present — Nemotron often
            # streams a partial answer in content alongside tools, which breaks
            # the tool loop and can interleave with local tool trace output.
            api_messages.append(
                {
                    "role": "assistant",
                    "content": "" if response.tool_calls else (response.content or ""),
                    "tool_calls": assistant_tool_calls,
                }
            )

            for tool_call in response.tool_calls:
                result = execute_tool(tool_call.name, tool_call.arguments)
                if result.success:
                    logger.info(
                        "[chat] Tool %s OK — %s",
                        tool_call.name,
                        json.dumps(result.result)[:200],
                    )
                else:
                    logger.warning(
                        "[chat] Tool %s failed: %s (args=%s)",
                        tool_call.name,
                        result.error,
                        json.dumps(tool_call.arguments),
                    )
                api_messages.append(
                    {
                        "role": "tool",
                        "content": tool_result_to_json(result),
                        "tool_call_id": tool_call.id,
                    }
                )

        if food_log_requested and not turn_had_successful_tool(api_messages, "log_food"):
            context_text = recent_user_text(user_message, extra_messages)
            description = extract_food_description(context_text)
            if description:
                meal_type = infer_meal_type_from_text(context_text)
                fallback = execute_tool(
                    "log_food",
                    {
                        "meal_type": meal_type,
                        "food_description": description,
                    },
                )
                if fallback.success:
                    logger.info(
                        "[chat] Fallback log_food saved %s: %s",
                        meal_type,
                        description,
                    )
                    return (
                        f"Got it — I've logged your {meal_type}: {description}. "
                        "You can see it on the Nutrition tab."
                    )
                logger.warning("[chat] Fallback log_food failed: %s", fallback.error)

        return self._final_chat_reply_with_schema(api_messages)

    def _final_chat_reply_with_schema(
        self,
        api_messages: list[ChatCompletionMessageParam],
    ) -> str:
        """Force the final coach answer through the strict reply schema.

        Mirrors the onboarding/daily-planner approach: constrained decoding
        guarantees shape, then the reply field is validated before use.
        """
        constrained_messages = list(api_messages)
        constrained_messages.append(
            {
                "role": "user",
                "content": (
                    "Respond to the conversation above with your final answer as JSON "
                    'matching {"reply": "<your answer>"}. The reply must be plain '
                    "conversational text — no tool calls, no nested JSON."
                ),
            }
        )
        response: LlmResponse | None = None
        try:
            response = self._run_completion_on_api_messages(
                constrained_messages,
                use_tools=False,
                json_mode=True,
                json_schema=COACH_REPLY_JSON_SCHEMA,
            )
        except Exception as error:
            # Older vLLM builds reject json_schema response_format; fall back.
            logger.warning(
                "[chat] json_schema response_format failed (%s); "
                "falling back to json_object mode.",
                error,
            )
        if response is None:
            response = self._run_completion_on_api_messages(
                constrained_messages,
                use_tools=False,
                json_mode=True,
            )
        try:
            payload = self._parse_json_object(response.content, log_context="chat")
            reply_value = payload.get("reply")
            if isinstance(reply_value, str):
                validated_reply = _validate_chat_reply(reply_value)
                if validated_reply is not None:
                    return validated_reply
        except ValueError as error:
            logger.warning("[chat] Schema-constrained reply was not valid JSON: %s", error)

        # Last resort: surface whatever text exists rather than an empty bubble.
        fallback = response.content.strip()
        if fallback:
            return fallback
        return "I couldn't put together a reply just now. Please try again."

    def generate_json(
        self,
        user_prompt: str,
        system_addition: str = "",
        retry_once: bool = True,
    ) -> dict[str, object]:
        """Request structured JSON output with validation and one retry."""
        system_prompt = trim_prompt_to_budget(
            build_system_prompt(include_tools_instruction=False),
            self.config.context_limit_tokens,
        )
        if system_addition.strip():
            system_prompt = f"{system_prompt}\n\n{system_addition.strip()}"

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]

        last_error = "Unknown JSON parse error."
        attempts = 2 if retry_once else 1
        for attempt in range(attempts):
            response = self.complete(messages, use_tools=False, json_mode=True)
            try:
                return self._parse_json_object(response.content)
            except ValueError as error:
                last_error = str(error)
                if attempt + 1 >= attempts:
                    break
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "Your previous response was not valid JSON matching the required "
                            f"schema. Error: {last_error}. Return ONLY valid JSON."
                        ),
                    )
                )

        raise ValueError(f"Failed to parse JSON from LLM response: {last_error}")

    def generate_onboarding_json(
        self,
        user_prompt: str,
        system_prompt: str,
        retry_once: bool = True,
        json_schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Request onboarding JSON using a dedicated system prompt (no runtime DB context)."""
        from llm.system_prompt import ROLE_DEFINITION, SAFETY_GUARDRAIL

        full_system = trim_prompt_to_budget(
            f"{ROLE_DEFINITION}\n\n{SAFETY_GUARDRAIL}\n\n{system_prompt.strip()}",
            self.config.context_limit_tokens,
        )
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=full_system),
            ChatMessage(role="user", content=user_prompt),
        ]

        last_error = "Unknown JSON parse error."
        attempts = 2 if retry_once else 1
        schema_supported = json_schema is not None
        for attempt in range(attempts):
            response: LlmResponse | None = None
            if schema_supported and json_schema is not None:
                try:
                    response = self.complete(
                        messages,
                        use_tools=False,
                        json_mode=True,
                        json_schema=json_schema,
                    )
                except Exception as error:
                    # Older vLLM builds reject json_schema; fall back permanently.
                    logger.warning(
                        "[onboarding] json_schema response_format failed (%s); "
                        "falling back to json_object mode.",
                        error,
                    )
                    schema_supported = False
            if response is None:
                response = self.complete(messages, use_tools=False, json_mode=True)
            try:
                return self._parse_json_object(response.content, log_context="onboarding")
            except ValueError as error:
                last_error = str(error)
                if attempt + 1 >= attempts:
                    break
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "Your previous response was not valid JSON matching the required "
                            f"schema. Error: {last_error}. Return ONLY valid JSON."
                        ),
                    )
                )

        raise ValueError(f"Failed to parse JSON from LLM response: {last_error}")

    def _build_daily_schedule_messages(
        self,
        full_system: str,
        user_prompt: str,
        last_error: str,
        attempt: int,
    ) -> list[ChatCompletionMessageParam]:
        """Start a fresh message list for one daily-plan attempt."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_prompt},
        ]
        if attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON plan failed to parse. "
                        f"Error: {last_error}. "
                        "Return ONE compact valid JSON object: 5 hydration jobs, "
                        "3 meals, 1 exercise. Keep each context under 60 characters. "
                        "No markdown, no commentary."
                    ),
                }
            )
        return messages

    def _request_daily_plan_json(
        self,
        api_messages: list[ChatCompletionMessageParam],
        json_schema: dict[str, object] | None,
    ) -> dict[str, object]:
        """Request the daily-plan JSON, preferring schema-constrained decoding."""
        schedule_tokens = self.config.daily_schedule_max_tokens

        json_response: LlmResponse | None = None
        if json_schema is not None:
            try:
                json_response = self._run_completion_on_api_messages(
                    api_messages,
                    use_tools=False,
                    json_mode=True,
                    max_tokens=schedule_tokens,
                    json_schema=json_schema,
                )
            except Exception as error:
                # Older vLLM builds reject json_schema response_format; fall back.
                logger.warning(
                    "[daily_schedule] json_schema response_format failed (%s); "
                    "falling back to json_object mode.",
                    error,
                )
                json_response = None

        if json_response is None:
            json_response = self._run_completion_on_api_messages(
                api_messages,
                use_tools=False,
                json_mode=True,
                max_tokens=schedule_tokens,
            )

        if not json_response.content.strip():
            logger.warning(
                "[daily_schedule] json_mode returned empty content; retrying without response_format."
            )
            api_messages.append(
                {
                    "role": "user",
                    "content": "Output ONLY the JSON object. No other text before or after.",
                }
            )
            json_response = self._run_completion_on_api_messages(
                api_messages,
                use_tools=False,
                json_mode=False,
                max_tokens=schedule_tokens,
            )
        if json_response.raw_finish_reason == "length":
            preview = json_response.content[:300]
            logger.warning(
                "[daily_schedule] JSON truncated at %s tokens. Preview: %s...",
                schedule_tokens,
                preview,
            )
            raise ValueError(
                f"JSON response truncated (max_tokens={schedule_tokens})."
            )
        return self._parse_json_object(json_response.content, log_context="daily_schedule")

    def generate_daily_schedule_json(
        self,
        user_prompt: str,
        system_prompt: str,
        json_schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate the daily plan in one schema-constrained call with retries.

        All planning context (profile, frameworks, meds, logs, weather) is
        embedded in the user prompt, so no tool loop is needed — this keeps
        the full 8K context window available for the JSON output.
        """
        from llm.system_prompt import ROLE_DEFINITION, SAFETY_GUARDRAIL

        full_system = trim_prompt_to_budget(
            f"{ROLE_DEFINITION}\n\n{SAFETY_GUARDRAIL}\n\n{system_prompt.strip()}",
            self.config.context_limit_tokens,
        )

        last_error = "Unknown daily schedule error."
        attempts = self.config.daily_schedule_max_attempts
        for attempt in range(attempts):
            api_messages = self._build_daily_schedule_messages(
                full_system,
                user_prompt,
                last_error,
                attempt,
            )
            try:
                return self._request_daily_plan_json(api_messages, json_schema)
            except Exception as error:
                last_error = str(error)
                logger.warning(
                    "[daily_schedule] Attempt %s/%s failed: %s",
                    attempt + 1,
                    attempts,
                    last_error,
                )
                if attempt + 1 >= attempts:
                    break
                delay = self.config.daily_schedule_retry_delay_seconds
                logger.info(
                    "[daily_schedule] Retrying in %ss with a fresh context...",
                    delay,
                )
                time.sleep(delay)

        raise ValueError(f"Daily schedule LLM failed after {attempts} attempts: {last_error}")

    def _parse_json_object(
        self,
        content: str,
        log_context: str = "llm",
    ) -> dict[str, object]:
        """Extract and validate a JSON object from model output."""
        stripped = content.strip()
        if not stripped:
            logger.warning("[%s] Empty JSON response from model.", log_context)
            raise ValueError("Empty JSON response.")

        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return cast(dict[str, object], parsed)
        except json.JSONDecodeError as error:
            logger.warning(
                "[%s] JSON parse error: %s. Response preview: %s",
                log_context,
                error,
                stripped[:400],
            )

        match = _JSON_BLOCK_PATTERN.search(stripped)
        if match is None:
            raise ValueError("No JSON object found in response.")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as error:
            logger.warning(
                "[%s] Extracted JSON block still invalid: %s. Block preview: %s",
                log_context,
                error,
                match.group(0)[:400],
            )
            raise ValueError(f"Malformed JSON in response: {error}") from error
        if not isinstance(parsed, dict):
            raise ValueError("JSON response must be an object.")
        return cast(dict[str, object], parsed)


_client: LlmClient | None = None


def get_llm_client() -> LlmClient:
    """Return the shared LLM client instance."""
    global _client
    if _client is None:
        _client = LlmClient()
    return _client


def reset_llm_client() -> None:
    """Reset the shared client (used by tests)."""
    global _client
    _client = None
