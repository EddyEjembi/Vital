"""Home tab — greeting, checklist, metrics, and daily check-in."""

from datetime import date, datetime

import gradio as gr

from db import queries
from vital_types.db import DailyLogSchemaField, MedicationLogEntry


def _period_label(now: datetime) -> str:
    """Return morning, afternoon, or evening for the current hour."""
    if now.hour < 12:
        return "morning"
    if now.hour < 17:
        return "afternoon"
    return "evening"


def _greeting_text() -> str:
    """Build the dashboard greeting line."""
    profile = queries.get_profile()
    name = profile.name if profile and profile.name.strip() else "there"
    today = date.today().strftime("%A, %B %d, %Y")
    period = _period_label(datetime.now())
    return f"## Good {period}, {name}\n{today}"


def _briefing_text() -> str:
    """Return the cached morning briefing or a short fallback."""
    cached = queries.get_morning_briefing_cache()
    if cached:
        return cached
    profile = queries.get_profile()
    if profile is None:
        return "Your coach briefing will appear here after the morning greeting."
    return (
        f"Welcome back, {profile.name}. Use today's check-in below to log how you're feeling."
    )


def _metrics_markdown() -> str:
    """Build the three-column metrics summary as markdown."""
    today = date.today()
    logs = {entry.field_id: entry.value for entry in queries.get_daily_logs_for_date(today)}
    medications = queries.get_medications_for_date(today)
    taken_count = sum(1 for med in medications if med.taken)
    total_meds = len(medications)

    primary_metric = "—"
    schema = queries.get_daily_log_schema()
    if schema:
        first_field = schema[0]
        primary_metric = logs.get(first_field.field_id, "—")

    water_value = logs.get("water_cups", "0")
    return (
        f"| Health metric | Water (cups) | Meds taken |\n"
        f"|---|---|---|\n"
        f"| **{primary_metric}** | **{water_value}** | **{taken_count}/{total_meds}** |"
    )


def _schedule_markdown() -> str:
    """Build today's LLM-generated schedule summary."""
    plan = queries.get_daily_plan(date.today())
    if plan is None:
        return "_Today's schedule will appear after your wake time._"
    lines = [f"_{plan.summary}_\n"]
    for job in plan.jobs:
        extra = ""
        if job.volume_ml:
            extra = f" ({job.volume_ml}ml)"
        if job.exercise_type:
            extra = f" — {job.exercise_type}, {job.duration_minutes} min"
        lines.append(f"- **{job.time}** [{job.type}] {job.message}{extra}")
    return "\n".join(lines)


def _medication_choice_label(medication: MedicationLogEntry) -> str:
    """Format one medication row for the interactive checklist."""
    return (
        f"{medication.scheduled_time} — "
        f"{medication.medication_name} ({medication.dose})"
    )


def empty_medication_checklist_update() -> dict[str, object]:
    """Return a safe CheckboxGroup update when the dashboard is hidden."""
    return {
        "choices": [],
        "value": [],
        "label": "Medications today",
        "info": "",
    }


def medication_checklist_update() -> dict[str, object]:
    """Build a gr.update payload for today's medication checklist."""
    medications = queries.get_medications_for_date(date.today())
    if not medications:
        return {
            "choices": [],
            "value": [],
            "label": "Medications today",
            "info": "No medications scheduled today.",
        }
    choices = [_medication_choice_label(med) for med in medications]
    selected = [
        label for med, label in zip(medications, choices, strict=True) if med.taken
    ]
    return {
        "choices": choices,
        "value": selected,
        "label": "Medications today",
        "info": "Check each dose when you take it.",
    }


def get_dashboard_updates() -> tuple[str, str, str, dict[str, object]]:
    """Return fresh dashboard content from the database."""
    return (
        _greeting_text(),
        _briefing_text(),
        _metrics_markdown(),
        medication_checklist_update(),
    )


def _render_checkin_field(field: DailyLogSchemaField) -> gr.components.Component:
    """Render one dynamic check-in field from schema."""
    if field.type == "scale_1_10":
        return gr.Slider(
            minimum=1,
            maximum=10,
            step=1,
            value=5,
            label=field.label,
            info=field.reason,
        )
    if field.type == "number":
        return gr.Number(label=field.label, value=0, precision=0, info=field.reason)
    if field.type == "select":
        return gr.Radio(
            choices=field.options,
            label=field.label,
            info=field.reason,
        )
    if field.type == "boolean":
        return gr.Checkbox(label=field.label, value=False, info=field.reason)
    return gr.Textbox(label=field.label, info=field.reason)


def build_dashboard_tab(initial_onboarded: bool = False) -> dict[str, object]:
    """Build the home dashboard tab components."""
    greeting_text, briefing_text, metrics_text, checklist_payload = (
        get_dashboard_updates() if initial_onboarded else ("", "", "", {})
    )

    greeting = gr.Markdown(greeting_text)
    briefing = gr.Markdown(briefing_text, elem_classes=["coach-bubble"])
    gr.Markdown("### Today's schedule")
    schedule = gr.Markdown(_schedule_markdown() if initial_onboarded else "")
    metrics = gr.Markdown(metrics_text)

    gr.Markdown("### Medication checklist")
    med_checklist = gr.CheckboxGroup(
        choices=checklist_payload.get("choices", []),
        value=checklist_payload.get("value", []),
        label=str(checklist_payload.get("label", "Medications today")),
        info=str(checklist_payload.get("info", "")),
    )
    med_status = gr.Markdown("")

    gr.Markdown("### Daily check-in")
    schema = queries.get_daily_log_schema() if initial_onboarded else []
    checkin_fields: list[gr.components.Component] = []
    checkin_field_ids: list[str] = []

    with gr.Row():
        for field in schema:
            checkin_fields.append(_render_checkin_field(field))
            checkin_field_ids.append(field.field_id)

    save_status = gr.Markdown("")
    save_btn = gr.Button("Save check-in", variant="primary")
    refresh_btn = gr.Button("Refresh")

    def sync_medications(selected_labels: list[str]) -> tuple[str, str]:
        """Persist medication checklist checkbox changes to the database."""
        today = date.today()
        medications = queries.get_medications_for_date(today)
        selected_set = set(selected_labels)
        for medication in medications:
            label = _medication_choice_label(medication)
            should_be_taken = label in selected_set
            if medication.taken != should_be_taken:
                queries.set_medication_taken_status(
                    today,
                    medication.medication_name,
                    medication.scheduled_time,
                    should_be_taken,
                )
        return _metrics_markdown(), "**Medication checklist updated.**"

    def save_checkin(*values: object) -> str:
        """Persist dynamic check-in values to the database."""
        today = date.today()
        for index, field_id in enumerate(checkin_field_ids):
            if index >= len(values):
                continue
            raw_value = values[index]
            if isinstance(raw_value, bool):
                text_value = "true" if raw_value else "false"
            else:
                text_value = str(raw_value)
            queries.upsert_daily_log(today, field_id, text_value)
        return "**Check-in saved.**"

    def refresh_dashboard() -> tuple:
        """Reload dashboard summaries from the database."""
        greeting_value, briefing_value, metrics_value, checklist_payload = (
            get_dashboard_updates()
        )
        return (
            gr.update(value=greeting_value),
            gr.update(value=briefing_value),
            gr.update(value=_schedule_markdown()),
            gr.update(value=metrics_value),
            gr.update(**checklist_payload),
            "",
        )

    med_checklist.change(
        sync_medications,
        inputs=[med_checklist],
        outputs=[metrics, med_status],
    )

    if checkin_fields:
        save_btn.click(
            save_checkin,
            inputs=checkin_fields,
            outputs=[save_status],
        )

    refresh_btn.click(
        refresh_dashboard,
        outputs=[greeting, briefing, schedule, metrics, med_checklist, med_status],
    )

    return {
        "greeting": greeting,
        "briefing": briefing,
        "schedule": schedule,
        "metrics": metrics,
        "checklist": med_checklist,
        "refresh_dashboard": refresh_dashboard,
    }
