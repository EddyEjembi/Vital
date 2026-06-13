"""Run onboarding LLM calls with validation and safe fallbacks."""

import logging

from vital_types.db import ProfileInput
from vital_types.onboarding import FollowUpQuestion, OnboardingPlan

from llm.client import get_llm_client
from llm.onboarding import (
    CALL_1_SYSTEM,
    CALL_2_SYSTEM,
    FOLLOW_UP_JSON_SCHEMA,
    ONBOARDING_PLAN_JSON_SCHEMA,
    OnboardingValidationError,
    build_call_1_user_prompt,
    build_call_2_user_prompt,
    finalize_plan_for_profile,
    validate_follow_up_response,
    validate_plan_response,
)

logger = logging.getLogger(__name__)


def run_onboarding_call_1(profile: ProfileInput) -> list[FollowUpQuestion]:
    """Run Call 1 and return validated follow-up questions (empty on failure)."""
    client = get_llm_client()
    try:
        payload = client.generate_onboarding_json(
            build_call_1_user_prompt(profile),
            system_prompt=CALL_1_SYSTEM,
            retry_once=True,
            json_schema=FOLLOW_UP_JSON_SCHEMA,
        )
        return validate_follow_up_response(payload)
    except (ValueError, OnboardingValidationError) as error:
        logger.warning("Onboarding Call 1 failed after retry — skipping follow-ups: %s", error)
        return []


def run_onboarding_call_2(
    profile: ProfileInput,
    follow_up_answers: dict[str, str],
    additional_notes: str,
) -> OnboardingPlan:
    """Run Call 2 and return a validated onboarding plan."""
    client = get_llm_client()
    try:
        payload = client.generate_onboarding_json(
            build_call_2_user_prompt(profile, follow_up_answers, additional_notes),
            system_prompt=CALL_2_SYSTEM,
            retry_once=True,
            json_schema=ONBOARDING_PLAN_JSON_SCHEMA,
        )
        plan = validate_plan_response(payload)
        return finalize_plan_for_profile(profile, plan)
    except (ValueError, OnboardingValidationError) as error:
        raise ValueError(f"Plan generation failed: {error}") from error
