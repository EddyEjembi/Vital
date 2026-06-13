"""
Live LLM evaluation against your Modal Nemotron endpoint.

Seeds the local DB with sample data so tool calls return real results.

Prerequisites:
    modal deploy infra/vllm_serve.py
    Set VITAL_LLM_BASE_URL and VITAL_MODEL_ID in .env

Usage:
    uv run python -m scripts.eval_llm
"""

import json
import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import patch

from db import queries
from db.database import reset_database
from llm.client import get_llm_client
from llm.config import get_llm_config
from llm.tool_runner import execute_tool
from vital_types.db import MedicationLogEntry, MedicationRecord, ProfileInput


def seed_eval_database() -> None:
    """Insert a realistic Amara-like profile and today's logs for tool testing."""
    queries.save_profile(
        ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
            conditions=["sickle cell disease"],
            medications=[
                MedicationRecord(name="Folic acid", dose="5mg", time="08:00"),
                MedicationRecord(name="Vitamin C", dose="500mg", time="18:00"),
            ],
            triggers=["dehydration", "cold temperatures", "stress"],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="Avoid processed sugar",
            local_foods="eba, egusi soup, beans, oranges",
        )
    )

    today = date.today()
    queries.insert_medication_log(
        MedicationLogEntry(
            date=today,
            medication_name="Folic acid",
            dose="5mg",
            scheduled_time="08:00",
            taken=True,
            taken_at=datetime.now(timezone.utc),
        )
    )
    queries.insert_medication_log(
        MedicationLogEntry(
            date=today,
            medication_name="Vitamin C",
            dose="500mg",
            scheduled_time="18:00",
            taken=False,
            taken_at=None,
        )
    )
    queries.upsert_daily_log(today, "pain_level", "3")
    queries.upsert_daily_log(today, "water_cups", "6")
    queries.upsert_daily_log(today, "energy_level", "7")


def print_db_snapshot(label: str) -> None:
    """Print medications and daily logs currently stored in SQLite."""
    today = date.today()
    medications = queries.get_medications_for_date(today)
    logs = queries.get_daily_logs_for_date(today)

    print(f"   {label}")
    print(f"   - Medications today ({len(medications)}):")
    for med in medications:
        status = "taken" if med.taken else "pending"
        print(f"       {med.scheduled_time}  {med.medication_name} ({med.dose}) — {status}")
    print(f"   - Daily logs today ({len(logs)}):")
    for entry in logs:
        print(f"       {entry.field_id}: {entry.value}")
    print()


def print_tool_trace(tool_name: str, arguments: dict[str, object]):
    """Print when the LLM triggers a tool during eval and return the real result."""
    print(f"   [TOOL CALLED] {tool_name}({json.dumps(arguments)})", flush=True)
    result = execute_tool(tool_name, arguments)
    print(f"   [TOOL SUCCESS] {result.success}", flush=True)
    if result.success:
        result_text = json.dumps(result.result, indent=2, ensure_ascii=False)
        print("   [TOOL RESULT]", flush=True)
        for line in result_text.splitlines():
            print(f"   | {line}", flush=True)
    if result.error:
        print(f"   [TOOL ERROR]  {result.error}", flush=True)
    return result


def medication_is_taken(medication_name: str, scheduled_time: str) -> bool:
    """Return whether a scheduled dose is marked taken in today's medication log."""
    today = date.today()
    for med in queries.get_medications_for_date(today):
        if med.medication_name == medication_name and med.scheduled_time == scheduled_time:
            return med.taken
    return False


def main() -> None:
    """Run live checks: chat, JSON, read tools, write tools (with DB verify)."""
    config = get_llm_config()
    if "localhost" in config.base_url and not os.getenv("VITAL_LLM_BASE_URL"):
        print("Set VITAL_LLM_BASE_URL in .env to your Modal /v1 endpoint first.")
        sys.exit(1)

    skip_reset = os.getenv("VITAL_EVAL_SKIP_RESET", "").lower() in ("1", "true", "yes")
    if skip_reset:
        print("WARNING: VITAL_EVAL_SKIP_RESET is set — using existing DB (may be stale).\n")
    else:
        reset_database()
        seed_eval_database()

    client = get_llm_client()

    print("=" * 60)
    print("1) Simple chat (no tools)")
    print("=" * 60)
    reply = client.chat("Write a short story about a cat in 20 words.", use_tools=False)
    print(f"   Reply: {reply}\n")

    print("=" * 60)
    print("2) JSON output — onboarding Call 1 (realistic)")
    print("=" * 60)
    onboarding_prompt = """
You are generating adaptive follow-up questions for a new Vitál user.

Profile:
- Name: Amara, age 24, Port Harcourt
- Condition: sickle cell disease (HbSS)
- Medications: Folic acid 5mg at 08:00, Vitamin C 500mg at 18:00
- Triggers: dehydration, cold, stress
- Goal: manage health condition, fewer pain crises
- Desk worker, light exercise, local foods: eba, egusi, beans

Return ONLY valid JSON with this exact shape (max 3 questions):
{
  "follow_up_questions": [
    {
      "question_id": "snake_case_id",
      "question": "question text for the user",
      "type": "number",
      "reason": "why this question matters"
    }
  ]
}

Use type as one of: number, text. If no follow-ups needed, return an empty array.
"""
    payload = client.generate_json(
        onboarding_prompt,
        system_addition="Return ONLY valid JSON. No markdown fences. No extra keys.",
    )
    print("   Full JSON response:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print()

    print("=" * 60)
    print("3) Tool call — read medications (seeded DB)")
    print("=" * 60)
    print_db_snapshot("DB before LLM (read test):")

    with patch("llm.client.execute_tool", side_effect=print_tool_trace):
        tool_reply = client.chat(
            "What medications do I have scheduled today and which ones have I already taken?",
            use_tools=True,
        )

    print("   LLM final answer:")
    print(f"   {tool_reply}\n")

    print("=" * 60)
    print("4) Tool call — write to DB (log_medication_taken)")
    print("=" * 60)
    print_db_snapshot("DB before LLM (write test):")
    print("   Expect: Vitamin C 18:00 is pending; LLM should call log_medication_taken.")
    print("   Guardrails: TOOL_NAMES whitelist, HH:MM time, non-empty medication_name,")
    print("   mark_medication_taken only updates matching rows (updated: true/false).\n")

    with patch("llm.client.execute_tool", side_effect=print_tool_trace):
        write_reply = client.chat(
            "I just took my evening Vitamin C 500mg dose scheduled for 18:00. "
            "Please log it as taken in my medication log.",
            use_tools=True,
        )

    print("   LLM final answer:")
    print(f"   {write_reply}\n")
    print_db_snapshot("DB after LLM (write test):")

    vitamin_c_taken = medication_is_taken("Vitamin C", "18:00")
    if vitamin_c_taken:
        print("   [PASS] DB verify: Vitamin C 18:00 is now marked taken.")
    else:
        print("   [FAIL] DB verify: Vitamin C 18:00 is still pending.")
        print("     Check [TOOL CALLED] above — validation may have rejected bad args.")

    print("\nAll eval checks completed.")


if __name__ == "__main__":
    main()
