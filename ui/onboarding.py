"""First-run conversational onboarding flow."""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import gradio as gr

from core.personalization import build_desk_break_message
from db import queries
from llm.onboarding import plan_to_commit_data, profile_to_prompt_dict
from ui.dashboard import get_dashboard_updates
from llm.onboarding_flow import run_onboarding_call_1, run_onboarding_call_2
from vital_types.db import MedicationRecord, ProfileInput
from vital_types.onboarding import OnboardingPlan

logger = logging.getLogger(__name__)

_GOAL_CHOICES = [
    "Manage a health condition",
    "Build better daily habits",
    "Improve energy and focus",
    "Lose weight / get fitter",
    "General wellness",
]
_EXERCISE_CHOICES = ["none", "light", "moderate", "active"]
_MEDICATION_LINE_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+(?P<dose>\S+)\s+at\s+(?P<time>\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)

def _split_csv_lines(value: str) -> list[str]:
    """Split comma or newline separated values into a clean list."""
    if not value.strip():
        return []
    parts = re.split(r"[,;\n]+", value)
    return [part.strip() for part in parts if part.strip()]


def _parse_medications(raw_text: str) -> list[MedicationRecord]:
    """Parse medication lines like 'Folic acid 5mg at 08:00'."""
    medications: list[MedicationRecord] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _MEDICATION_LINE_PATTERN.match(stripped)
        if match is None:
            raise ValueError(
                f"Invalid medication line: '{stripped}'. "
                "Use format: Name dose at HH:MM"
            )
        time_value = match.group("time")
        if len(time_value.split(":")[0]) == 1:
            time_value = f"0{time_value}"
        medications.append(
            MedicationRecord(
                name=match.group("name").strip(),
                dose=match.group("dose").strip(),
                time=time_value,
            )
        )
    return medications


def _profile_from_session(data: object) -> ProfileInput | None:
    """Rebuild a ProfileInput from Gradio session state (dict-safe)."""
    if data is None:
        return None
    if isinstance(data, ProfileInput):
        return data
    if not isinstance(data, dict):
        return None

    raw_medications = data.get("medications", [])
    medications: list[MedicationRecord] = []
    if isinstance(raw_medications, list):
        for item in raw_medications:
            if not isinstance(item, dict):
                continue
            medications.append(
                MedicationRecord(
                    name=str(item.get("name", "")),
                    dose=str(item.get("dose", "")),
                    time=str(item.get("time", "")),
                )
            )

    return ProfileInput(
        name=str(data.get("name", "")),
        age=int(data.get("age", 0)),
        city=str(data.get("city", "")),
        profession=str(data.get("profession", "")),
        goal=str(data.get("goal", "")),
        conditions=[str(item) for item in data.get("conditions", []) if str(item).strip()],
        medications=medications,
        triggers=[str(item) for item in data.get("triggers", []) if str(item).strip()],
        wake_time=str(data.get("wake_time", "07:00")),
        sleep_time=str(data.get("sleep_time", "23:00")),
        desk_worker=bool(data.get("desk_worker", False)),
        exercise_level=str(data.get("exercise_level", "light")),
        dietary_notes=str(data.get("dietary_notes", "")),
        local_foods=str(data.get("local_foods", "")),
    )


def _profile_from_form(
    name: str,
    age: int,
    city: str,
    profession: str,
    goal: str,
    conditions_text: str,
    medications_text: str,
    triggers_text: str,
    wake_time: str,
    sleep_time: str,
    desk_worker: bool,
    exercise_level: str,
    dietary_notes: str,
    local_foods: str,
) -> ProfileInput:
    """Build a ProfileInput from onboarding form values."""
    if not name.strip():
        raise ValueError("Name is required.")
    if not city.strip():
        raise ValueError("City is required.")
    if not profession.strip():
        raise ValueError("Profession is required.")
    if age < 1 or age > 120:
        raise ValueError("Age must be between 1 and 120.")

    return ProfileInput(
        name=name.strip(),
        age=int(age),
        city=city.strip(),
        profession=profession.strip(),
        goal=goal,
        conditions=_split_csv_lines(conditions_text),
        medications=_parse_medications(medications_text),
        triggers=_split_csv_lines(triggers_text),
        wake_time=wake_time.strip() or "07:00",
        sleep_time=sleep_time.strip() or "23:00",
        desk_worker=desk_worker,
        exercise_level=exercise_level,
        dietary_notes=dietary_notes.strip(),
        local_foods=local_foods.strip(),
    )


def _format_plan_review(plan: OnboardingPlan) -> str:
    """Format the generated plan for human review."""
    check_in_lines = [
        f"- **{field.label}** (`{field.field_id}`, {field.type}) — {field.reason}"
        for field in plan.daily_log_fields
    ]
    job_lines = [
        f"- **{job.time or job.interval_minutes}** [{job.type}] {job.message}"
        + (" (TTS)" if job.tts else "")
        for job in plan.scheduled_jobs
    ]
    framework = plan.meal_plan_framework
    exercise = plan.exercise_plan
    presence = plan.presence_check

    return (
        "### Daily check-in fields\n"
        + ("\n".join(check_in_lines) if check_in_lines else "- (none)")
        + f"\n\n### Hydration goal\n- **{plan.hydration_goal_liters}L per day** "
        + "(split into timed reminders each morning)\n\n"
        + "### Medication reminders\n"
        + ("\n".join(job_lines) if job_lines else "- (none — daily water & exercise are planned each morning)")
        + "\n\n### Nutrition framework\n"
        + f"- Prioritise: {', '.join(framework.nutrients_to_prioritise) or '—'}\n"
        + f"- Moderate: {', '.join(framework.nutrients_to_moderate) or '—'}\n"
        + f"- Notes: {framework.notes or '—'}\n\n"
        + "### Exercise plan\n"
        + f"- {exercise.frequency}, {exercise.intensity}, {exercise.session_duration_minutes} min\n"
        + f"- Types: {', '.join(exercise.types) or '—'}\n"
        + f"- Avoid: {', '.join(exercise.avoid) or '—'}\n"
        + f"- Notes: {exercise.notes or '—'}\n\n"
        + "### Desk break presence\n"
        + f"- Enabled: {presence.enabled}\n"
        + f"- Break after: {presence.max_continuous_minutes} minutes\n"
        + f"- Message: {presence.break_message}\n\n"
        + "### Coach instructions\n"
        + (plan.system_prompt_additions or "(none)")
    )


def _plan_to_session(plan: OnboardingPlan) -> dict[str, object]:
    """Serialize an onboarding plan for Gradio session state."""
    return {
        "daily_log_fields": [asdict(field) for field in plan.daily_log_fields],
        "scheduled_jobs": [asdict(job) for job in plan.scheduled_jobs],
        "meal_plan_framework": asdict(plan.meal_plan_framework),
        "exercise_plan": asdict(plan.exercise_plan),
        "weekly_check_structure": asdict(plan.weekly_check_structure),
        "presence_check": asdict(plan.presence_check),
        "system_prompt_additions": plan.system_prompt_additions,
        "hydration_goal_liters": plan.hydration_goal_liters,
        "coach_quick_questions": plan.coach_quick_questions,
    }


def _plan_from_state(plan_dict: dict[str, Any] | None) -> OnboardingPlan | None:
    """Deserialize an OnboardingPlan stored in session state."""
    if plan_dict is None:
        return None
    from llm.onboarding import validate_plan_response

    return validate_plan_response(plan_dict)


def _hide_follow_ups() -> tuple[gr.update, gr.update, gr.update, gr.update, gr.update, gr.update]:
    """Return updates that hide all dynamic follow-up fields."""
    return (
        gr.update(value="", visible=False),
        gr.update(visible=False, value=""),
        gr.update(value="", visible=False),
        gr.update(visible=False, value=""),
        gr.update(value="", visible=False),
        gr.update(visible=False, value=""),
    )


def build_onboarding_ui(
    on_complete: Callable[[], None],
    dashboard_col: gr.components.Component,
    onboarding_col: gr.components.Component,
    dashboard_parts: dict[str, object],
) -> dict[str, gr.components.Component]:
    """Build the onboarding Gradio components and return named handles."""
    session_state = gr.State(
        {
            "profile": None,
            "follow_ups": [],
            "plan": None,
            "additional_notes": "",
        }
    )

    status_log = gr.Markdown(
        "### Status\nFill in your profile and click **Continue**.",
        elem_id="vital-status",
    )
    # Plan review lives outside step4_col so Gradio applies content while visible.
    # Updating Markdown inside a hidden Column often fails to render on show.
    plan_review = gr.Markdown("", visible=False, elem_id="vital-plan-review")

    with gr.Column(visible=True) as step1_col:
        gr.Markdown("## Welcome to Vitál\nLet's build your personal wellness plan.")
        gr.Markdown("**Step 1 of 4** — Tell us about yourself")
        name = gr.Textbox(label="Name")
        age = gr.Number(label="Age", value=24, precision=0)
        city = gr.Textbox(label="City / location", placeholder="Abuja")
        profession = gr.Textbox(
            label="Profession / work",
            placeholder="Software engineer, Banker, Cashier...",
        )
        goal = gr.Dropdown(label="Primary goal", choices=_GOAL_CHOICES, value=_GOAL_CHOICES[0])
        conditions_text = gr.Textbox(
            label="Health conditions (comma-separated)",
            placeholder="Asthma, Hypertension",
        )
        medications_text = gr.Textbox(
            label="Medications & supplements (one per line, optional)",
            placeholder="Folic acid 5mg at 08:00",
            lines=3,
        )
        triggers_text = gr.Textbox(
            label="Known triggers or things to avoid",
            placeholder="dehydration, cold temperatures",
        )
        wake_time = gr.Textbox(label="Wake time (HH:MM)", value="07:00")
        sleep_time = gr.Textbox(label="Sleep time (HH:MM)", value="23:00")
        desk_worker = gr.Checkbox(label="I work at a computer most of the day", value=True)
        exercise_level = gr.Dropdown(
            label="Current exercise level",
            choices=_EXERCISE_CHOICES,
            value="light",
        )
        dietary_notes = gr.Textbox(label="Dietary restrictions or preferences")
        local_foods = gr.Textbox(label="Local foods you typically eat")
        step1_error = gr.Markdown("")
        step1_btn = gr.Button("Continue", variant="primary")

    with gr.Column(visible=False) as step2_col:
        gr.Markdown("**Step 2 of 4** — A few tailored questions")
        step2_status = gr.Markdown("")
        fu_label_1 = gr.Markdown(visible=False)
        fu_input_1 = gr.Textbox(visible=False, label="")
        fu_label_2 = gr.Markdown(visible=False)
        fu_input_2 = gr.Textbox(visible=False, label="")
        fu_label_3 = gr.Markdown(visible=False)
        fu_input_3 = gr.Textbox(visible=False, label="")
        additional_notes = gr.Textbox(
            label="Anything else you want your coach to know? (optional)",
            lines=3,
            placeholder="Optional — goals, worries, preferences...",
        )
        step2_error = gr.Markdown("")
        step2_back = gr.Button("Back")
        step2_btn = gr.Button("Generate my plan", variant="primary")

    with gr.Column(visible=False) as step3_col:
        gr.Markdown("**Step 3 of 4** — Building your plan")
        step3_status = gr.Markdown(
            "⏳ **Please wait.** Vitál is working with the coach. This can take 30–60 seconds (or +10 minutes to load model)."
        )

    with gr.Column(visible=False) as step4_col:
        gr.Markdown("**Step 4 of 4** — Review and approve your plan")
        presence_minutes = gr.Number(
            label="Desk break reminder (minutes at desk)",
            value=30,
            precision=0,
        )
        presence_message = gr.Markdown(
            "_Desk break message is generated automatically when you approve._"
        )
        step4_error = gr.Markdown("")
        step4_back = gr.Button("Back")
        approve_btn = gr.Button("Approve & start Vitál", variant="primary")

    def go_step1() -> tuple:
        """Show step 1."""
        return (
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    def run_step1(
        session: dict[str, Any],
        name_value: str,
        age_value: float,
        city_value: str,
        profession_value: str,
        goal_value: str,
        conditions_value: str,
        medications_value: str,
        triggers_value: str,
        wake_value: str,
        sleep_value: str,
        desk_value: bool,
        exercise_value: str,
        diet_value: str,
        foods_value: str,
    ):
        """Validate profile, run Call 1, and show follow-up questions."""
        logger.info("Onboarding step 1: validating profile")
        yield (
            "### Status\n⏳ **Calling the coach...** Preparing follow-up questions (about 15–30 seconds).",
            session,
            gr.update(),
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
            *_hide_follow_ups(),
        )

        try:
            profile = _profile_from_form(
                name_value,
                int(age_value),
                city_value,
                profession_value,
                goal_value,
                conditions_value,
                medications_value,
                triggers_value,
                wake_value,
                sleep_value,
                desk_value,
                exercise_value,
                diet_value,
                foods_value,
            )
        except ValueError as error:
            logger.warning("Onboarding step 1 validation failed: %s", error)
            yield (
                f"### Status\n❌ **Error:** {error}",
                session,
                gr.update(),
                f"**Error:** {error}",
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                *_hide_follow_ups(),
            )
            return

        try:
            logger.info("Onboarding step 1: LLM Call 1 for %s", profile.name)
            follow_ups = run_onboarding_call_1(profile)
            logger.info("Onboarding step 1: received %s follow-up questions", len(follow_ups))
        except Exception as error:
            logger.exception("Onboarding Call 1 failed")
            yield (
                f"### Status\n❌ **Coach call failed:** {error}",
                session,
                gr.update(),
                f"**Error:** Could not reach the coach ({error}). Check your LLM connection.",
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                *_hide_follow_ups(),
            )
            return

        session = {
            "profile": profile_to_prompt_dict(profile),
            "follow_ups": [
                {
                    "question_id": item.question_id,
                    "question": item.question,
                    "type": item.type,
                    "reason": item.reason,
                }
                for item in follow_ups
            ],
            "plan": None,
            "additional_notes": "",
        }

        step2_message = (
            "We have a few follow-up questions based on your profile."
            if follow_ups
            else "No extra questions needed — add anything else below, then generate your plan."
        )
        status_message = (
            f"### Status\n✅ **Step 1 complete.** {len(follow_ups)} follow-up question(s) ready."
        )

        follow_components: list[gr.update] = []
        for index in range(3):
            if index < len(follow_ups):
                item = follow_ups[index]
                follow_components.extend(
                    [
                        gr.update(value=f"**{item.question}**", visible=True),
                        gr.update(label=item.question, visible=True, value=""),
                    ]
                )
            else:
                follow_components.extend(
                    [
                        gr.update(value="", visible=False),
                        gr.update(visible=False, value=""),
                    ]
                )

        yield (
            status_message,
            session,
            gr.update(value=step2_message),
            "",
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            *follow_components,
        )

    def run_step2(
        session: dict[str, Any],
        answer_1: str,
        answer_2: str,
        answer_3: str,
        notes: str,
    ):
        """Run Call 2 and show the plan review."""
        logger.info("Onboarding step 2: starting plan generation")
        yield (
            "### Status\n⏳ **Building your wellness plan...** This may take 30–60 seconds.",
            session,
            gr.update(value="", visible=False),
            gr.update(),
            gr.update(),
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
        )

        profile = _profile_from_session(session.get("profile"))
        if profile is None:
            logger.error("Onboarding step 2: profile missing from session")
            yield (
                "### Status\n❌ **Profile missing.** Go back to Step 1.",
                session,
                gr.update(value="", visible=False),
                gr.update(),
                gr.update(),
                "**Error:** Profile missing. Go back to Step 1.",
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
            )
            return

        follow_ups = session.get("follow_ups", [])
        answers: dict[str, str] = {}
        raw_answers = [answer_1, answer_2, answer_3]
        for index, item in enumerate(follow_ups):
            if index < len(raw_answers):
                answers[item["question_id"]] = raw_answers[index].strip()

        session["additional_notes"] = notes.strip()

        try:
            logger.info("Onboarding step 2: LLM Call 2 for %s", profile.name)
            plan = run_onboarding_call_2(profile, answers, notes)
            logger.info(
                "Onboarding step 2: plan ready (%s fields, %s jobs)",
                len(plan.daily_log_fields),
                len(plan.scheduled_jobs),
            )
        except ValueError as error:
            logger.warning("Onboarding Call 2 validation failed: %s", error)
            yield (
                f"### Status\n❌ **Plan validation failed:** {error}",
                session,
                gr.update(value="", visible=False),
                gr.update(),
                gr.update(),
                f"**Error:** {error}",
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
            )
            return
        except Exception as error:
            logger.exception("Onboarding Call 2 failed")
            yield (
                f"### Status\n❌ **Plan generation failed:** {error}",
                session,
                gr.update(value="", visible=False),
                gr.update(),
                gr.update(),
                f"**Error:** Plan generation failed ({error}). Please try again.",
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
            )
            return

        session["plan"] = _plan_to_session(plan)
        review_text = _format_plan_review(plan)
        desk_minutes = max(5, int(plan.presence_check.max_continuous_minutes))
        break_preview = build_desk_break_message(profile.name, desk_minutes)

        # Two yields: show step 4 shell first, then fill review (Gradio nested Column quirk).
        yield (
            "### Status\n✅ **Plan ready.** Review below and click **Approve & start Vitál**.",
            session,
            gr.update(value="Loading plan review...", visible=True),
            gr.update(value=plan.presence_check.max_continuous_minutes),
            gr.update(value=f"**Desk break message (spoken):** {break_preview}"),
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
        )
        yield (
            "### Status\n✅ **Plan ready.** Review below and click **Approve & start Vitál**.",
            session,
            gr.update(value=review_text, visible=True),
            gr.update(value=plan.presence_check.max_continuous_minutes),
            gr.update(value=f"**Desk break message (spoken):** {break_preview}"),
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
        )

    def run_approve(
        session: dict[str, Any],
        presence_minutes_value: float,
    ):
        """Commit the approved plan and finish onboarding."""
        logger.info("Onboarding step 4: saving plan")
        yield (
            "### Status\n⏳ **Saving your plan to the local database...**",
            session,
            "",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )

        profile = _profile_from_session(session.get("profile"))
        plan = _plan_from_state(session.get("plan"))
        if profile is None or plan is None:
            yield (
                "### Status\n❌ **Plan data missing.** Please go back.",
                session,
                "**Error:** Plan data missing. Please go back.",
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            )
            return

        desk_minutes = max(5, int(presence_minutes_value))
        plan.presence_check.max_continuous_minutes = desk_minutes
        plan.presence_check.break_message = build_desk_break_message(profile.name, desk_minutes)

        try:
            commit_data = plan_to_commit_data(profile, plan)
            queries.commit_onboarding_plan(commit_data)
            logger.info("Onboarding complete for %s", profile.name)
        except Exception as error:
            logger.exception("Onboarding commit failed")
            yield (
                f"### Status\n❌ **Save failed:** {error}",
                session,
                f"**Error saving plan:** {error}",
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            )
            return

        on_complete()
        greeting_value, briefing_value, metrics_value, checklist_payload = (
            get_dashboard_updates()
        )
        yield (
            "### Status\n✅ **All set!** Opening your dashboard...",
            session,
            "**Plan saved!** Starting Vitál...",
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(value=greeting_value),
            gr.update(value=briefing_value),
            gr.update(value=metrics_value),
            gr.update(**checklist_payload),
        )

    step1_btn.click(
        run_step1,
        inputs=[
            session_state,
            name,
            age,
            city,
            profession,
            goal,
            conditions_text,
            medications_text,
            triggers_text,
            wake_time,
            sleep_time,
            desk_worker,
            exercise_level,
            dietary_notes,
            local_foods,
        ],
        outputs=[
            status_log,
            session_state,
            step2_status,
            step1_error,
            step1_col,
            step2_col,
            step3_col,
            step4_col,
            fu_label_1,
            fu_input_1,
            fu_label_2,
            fu_input_2,
            fu_label_3,
            fu_input_3,
        ],
        show_progress="minimal",
    )

    step2_back.click(
        go_step1,
        outputs=[step1_col, step2_col, step3_col, step4_col],
    )

    step2_btn.click(
        run_step2,
        inputs=[session_state, fu_input_1, fu_input_2, fu_input_3, additional_notes],
        outputs=[
            status_log,
            session_state,
            plan_review,
            presence_minutes,
            presence_message,
            step4_error,
            step1_col,
            step2_col,
            step3_col,
            step4_col,
        ],
        show_progress="minimal",
    )

    step4_back.click(
        lambda: (
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(value="", visible=False),
        ),
        outputs=[step1_col, step2_col, step3_col, step4_col, plan_review],
    )

    approve_btn.click(
        run_approve,
        inputs=[session_state, presence_minutes],
        outputs=[
            status_log,
            session_state,
            step4_error,
            dashboard_col,
            onboarding_col,
            dashboard_parts["greeting"],
            dashboard_parts["briefing"],
            dashboard_parts["metrics"],
            dashboard_parts["checklist"],
        ],
        show_progress="full",
    )

    return {
        "step1_col": step1_col,
        "step2_col": step2_col,
        "step3_col": step3_col,
        "step4_col": step4_col,
        "session_state": session_state,
        "approve_btn": approve_btn,
    }
