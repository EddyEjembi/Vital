"""Movement tab — today's exercise plan and logging."""

from datetime import date, datetime, timezone

import gradio as gr

from db import queries
from vital_types.db import ExerciseLogEntry


def _exercise_plan_summary() -> str:
    """Show framework exercise plan from settings."""
    plan = queries.get_exercise_plan()
    if plan is None:
        return "_No exercise framework saved._"
    types = plan.get("types", [])
    types_text = ", ".join(types) if isinstance(types, list) else "—"
    return (
        f"**Framework:** {plan.get('frequency', '—')}, {plan.get('intensity', '—')}, "
        f"{plan.get('session_duration_minutes', '—')} min\n\n"
        f"**Types:** {types_text}\n\n"
        f"**Avoid:** {', '.join(plan.get('avoid', [])) if isinstance(plan.get('avoid'), list) else '—'}\n\n"
        f"**Notes:** {plan.get('notes', '—')}"
    )


def _todays_exercise_block() -> str:
    """Show today's scheduled exercise from the daily plan."""
    daily = queries.get_daily_plan(date.today())
    if daily is None:
        return "_Today's schedule not generated yet._"
    exercise_jobs = [job for job in daily.jobs if job.type == "exercise"]
    prep_jobs = [
        job for job in daily.jobs
        if job.type == "check_in" and job.job_id.startswith("exercise_prep_")
    ]
    if not exercise_jobs:
        return "_No exercise scheduled for today._"
    lines: list[str] = []
    for job in exercise_jobs:
        prep = next(
            (
                prep_job for prep_job in prep_jobs
                if prep_job.exercise_type == job.exercise_type
                or prep_job.job_id == f"exercise_prep_{job.job_id}"
            ),
            None,
        )
        prep_line = ""
        if prep is not None:
            prep_line = f"\n\n**Heads-up at {prep.time}:** {prep.message}"
        lines.append(
            f"### {job.time} — {job.exercise_type or 'exercise'}\n"
            f"{job.message}\n\n"
            f"Duration: **{job.duration_minutes or '—'}** min"
            f"{prep_line}\n\n"
            f"_{job.context}_"
        )
    return "\n\n".join(lines)


def _exercise_log_text() -> str:
    """Format today's completed exercises."""
    entries = queries.get_exercise_logs_for_date(date.today())
    if not entries:
        return "_Nothing logged yet today._"
    lines: list[str] = []
    for entry in entries:
        status = "done" if entry.completed else "pending"
        lines.append(
            f"- [{status}] {entry.exercise_type} — {entry.duration_minutes} min"
        )
    return "\n".join(lines)


def build_movement_tab() -> dict[str, gr.components.Component]:
    """Build the movement tab."""
    gr.Markdown("## Movement")
    today_block = gr.Markdown(_todays_exercise_block())
    framework = gr.Markdown(_exercise_plan_summary())
    gr.Markdown("### Completed today")
    log_display = gr.Markdown(_exercise_log_text())

    exercise_type = gr.Textbox(label="Exercise type", placeholder="walking")
    duration = gr.Number(label="Duration (minutes)", value=20, precision=0)
    log_status = gr.Markdown("")
    log_btn = gr.Button("Log it", variant="primary")
    refresh_btn = gr.Button("Refresh")

    def log_exercise(type_value: str, duration_value: float) -> tuple:
        """Log a completed exercise session."""
        if not type_value.strip():
            return gr.update(), "**Enter an exercise type.**"
        queries.insert_exercise_log(
            ExerciseLogEntry(
                date=date.today(),
                exercise_type=type_value.strip(),
                duration_minutes=max(1, int(duration_value)),
                completed=True,
                logged_at=datetime.now(timezone.utc),
            )
        )
        return gr.update(value=_exercise_log_text()), "**Exercise logged.**"

    log_btn.click(
        log_exercise,
        inputs=[exercise_type, duration],
        outputs=[log_display, log_status],
    )
    refresh_btn.click(
        lambda: (
            gr.update(value=_todays_exercise_block()),
            gr.update(value=_exercise_log_text()),
        ),
        outputs=[today_block, log_display],
    )

    return {"today_block": today_block}
