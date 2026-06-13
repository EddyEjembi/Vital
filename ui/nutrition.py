"""Nutrition tab — hydration progress, food log, meal framework."""

from datetime import date, datetime, timezone

import gradio as gr

from db import queries
from vital_types.db import FoodLogEntry


def _hydration_progress_text() -> str:
    """Build hydration progress from today's schedule and logs."""
    plan = queries.get_daily_plan(date.today())
    goal_liters = plan.hydration_goal_liters if plan else queries.get_hydration_goal_liters()
    logs = {entry.field_id: entry.value for entry in queries.get_daily_logs_for_date(date.today())}
    cups = int(logs.get("water_cups", "0") or "0")
    goal_ml = int(goal_liters * 1000)
    consumed_ml = cups * 250
    percent = min(100, int((consumed_ml / goal_ml) * 100)) if goal_ml > 0 else 0
    return (
        f"**Hydration target:** {goal_liters}L ({goal_ml}ml)\n\n"
        f"**Logged:** ~{consumed_ml}ml ({cups} cups) — {percent}% of goal"
    )


def _schedule_hydration_text() -> str:
    """List today's hydration reminders."""
    plan = queries.get_daily_plan(date.today())
    if plan is None:
        return "_Today's schedule not generated yet._"
    lines = [
        f"- **{job.time}** — {job.message}"
        + (f" ({job.volume_ml}ml)" if job.volume_ml else "")
        for job in plan.jobs
        if job.type == "hydration"
    ]
    return "\n".join(lines) if lines else "_No hydration reminders scheduled._"


def _food_log_text() -> str:
    """Format today's food log as markdown."""
    entries = queries.get_food_logs_for_date(date.today())
    if not entries:
        return "_No meals logged today._"
    lines: list[str] = []
    for entry in entries:
        note = f" — _{entry.llm_notes}_" if entry.llm_notes else ""
        lines.append(f"- **{entry.meal_type}:** {entry.food_description}{note}")
    return "\n".join(lines)


def _meal_plan_text() -> str:
    """Show today's meal suggestions from the daily schedule."""
    plan = queries.get_daily_plan(date.today())
    if plan is None:
        return "_Today's meal plan not generated yet._"
    meal_jobs = [job for job in plan.jobs if job.type == "meal"]
    if not meal_jobs:
        return "_No meals in today's schedule._"
    lines = [f"- **{job.time}** — {job.message}" for job in meal_jobs]
    return "### Today's meal plan\n" + "\n".join(lines)


def _framework_tags() -> str:
    """Show nutrients to prioritise from meal framework."""
    framework = queries.get_meal_plan_framework()
    if framework is None:
        return "_No nutrition framework saved._"
    prioritise = framework.get("nutrients_to_prioritise", [])
    moderate = framework.get("nutrients_to_moderate", [])
    notes = framework.get("notes", "")
    return (
        f"**Prioritise:** {', '.join(prioritise) if isinstance(prioritise, list) else '—'}\n\n"
        f"**Moderate:** {', '.join(moderate) if isinstance(moderate, list) else '—'}\n\n"
        f"**Notes:** {notes}"
    )


def build_nutrition_tab() -> dict[str, gr.components.Component]:
    """Build the nutrition tab."""
    gr.Markdown("## Nutrition")
    hydration = gr.Markdown(_hydration_progress_text())
    schedule = gr.Markdown(_schedule_hydration_text())
    meal_plan = gr.Markdown(_meal_plan_text())
    framework = gr.Markdown(_framework_tags())
    gr.Markdown("### Today's food log")
    food_log = gr.Markdown(_food_log_text())

    gr.Markdown("### Log a meal")
    meal_type = gr.Dropdown(
        choices=["breakfast", "lunch", "dinner", "snack"],
        label="Meal type",
        value="lunch",
    )
    food_description = gr.Textbox(label="What did you eat?", lines=2)
    water_cups = gr.Number(label="Update water cups today", value=0, precision=0)
    log_status = gr.Markdown("")
    log_btn = gr.Button("Save", variant="primary")
    refresh_btn = gr.Button("Refresh")

    def log_meal(meal: str, description: str, cups: float) -> tuple:
        """Log food and optional water count."""
        if description.strip():
            queries.insert_food_log(
                FoodLogEntry(
                    date=date.today(),
                    meal_type=meal,
                    food_description=description.strip(),
                    logged_at=datetime.now(timezone.utc),
                )
            )
        if cups >= 0:
            queries.log_water(int(cups))
        return (
            gr.update(value=_hydration_progress_text()),
            gr.update(value=_food_log_text()),
            "**Saved.**",
        )

    def refresh_view() -> tuple:
        """Reload nutrition displays."""
        return (
            gr.update(value=_hydration_progress_text()),
            gr.update(value=_schedule_hydration_text()),
            gr.update(value=_meal_plan_text()),
            gr.update(value=_food_log_text()),
        )

    log_btn.click(
        log_meal,
        inputs=[meal_type, food_description, water_cups],
        outputs=[hydration, food_log, log_status],
    )
    refresh_btn.click(refresh_view, outputs=[hydration, schedule, meal_plan, food_log])

    return {"hydration": hydration}
