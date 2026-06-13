"""Settings tab — profile, medications, hydration goal, presence, TTS."""

import logging
import re
from datetime import date

import gradio as gr

from core.daily_startup import ensure_today_schedule
from core.personalization import build_desk_break_message
from core.presence import restart_presence
from core.scheduler import reload_scheduler_jobs
from db import queries
from vital_types.db import MedicationRecord, PresenceConfig, ProfileInput
from vital_types.settings_prefs import TtsPreferences

logger = logging.getLogger(__name__)

_TIME_INPUT_PATTERN = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

_MEDICATION_LINE_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+(?P<dose>\S+)\s+at\s+(?P<time>\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)


def _medications_to_text(medications: list[MedicationRecord]) -> str:
    """Format medications for the settings text area."""
    return "\n".join(
        f"{item.name} {item.dose} at {item.time}" for item in medications
    )


def _parse_medications(raw_text: str) -> list[MedicationRecord]:
    """Parse medication lines from settings input."""
    medications: list[MedicationRecord] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _MEDICATION_LINE_PATTERN.match(stripped)
        if match is None:
            raise ValueError(
                f"Invalid medication line: '{stripped}'. "
                "Use format: Name 5mg at 08:00"
            )
        medications.append(
            MedicationRecord(
                name=match.group("name").strip(),
                dose=match.group("dose"),
                time=match.group("time"),
            )
        )
    return medications


def build_settings_refresh_updates() -> tuple:
    """Return fresh gr.update values for every settings field from the DB.

    Used on page load so a browser refresh always shows persisted values
    instead of the stale values captured when the app was built.
    """
    profile = queries.get_profile()
    presence = queries.get_presence_config()
    hydration_goal = queries.get_hydration_goal_liters()
    tts_prefs = queries.get_tts_preferences()
    break_minutes = presence.max_continuous_minutes if presence else 30
    break_preview = (
        build_desk_break_message(profile.name, break_minutes)
        if profile is not None
        else ""
    )
    return (
        gr.update(value=profile.name if profile else ""),
        gr.update(value=profile.wake_time if profile else "07:00"),
        gr.update(value=profile.sleep_time if profile else "23:00"),
        gr.update(value=hydration_goal),
        gr.update(value=_medications_to_text(profile.medications) if profile else ""),
        gr.update(value=break_minutes),
        gr.update(value=f"**Desk break message (auto):** _{break_preview}_"),
        gr.update(value=tts_prefs.hydration),
        gr.update(value=tts_prefs.exercise),
        gr.update(value=tts_prefs.medication),
        gr.update(value=tts_prefs.meal),
    )


def build_settings_tab() -> dict[str, gr.components.Component]:
    """Build the settings tab components."""
    profile = queries.get_profile()
    presence = queries.get_presence_config()
    hydration_goal = queries.get_hydration_goal_liters()
    tts_prefs = queries.get_tts_preferences()
    break_minutes = presence.max_continuous_minutes if presence else 30
    break_preview = (
        build_desk_break_message(profile.name, break_minutes)
        if profile is not None
        else ""
    )

    gr.Markdown("## Settings")
    name = gr.Textbox(label="Name", value=profile.name if profile else "")
    wake_time = gr.Textbox(label="Wake time (HH:MM)", value=profile.wake_time if profile else "07:00")
    sleep_time = gr.Textbox(label="Sleep time (HH:MM)", value=profile.sleep_time if profile else "23:00")
    hydration_liters = gr.Number(
        label="Daily hydration goal (litres)",
        value=hydration_goal,
        precision=1,
    )
    medications_text = gr.Textbox(
        label="Medications (one per line: Name 5mg at 08:00)",
        lines=4,
        value=_medications_to_text(profile.medications) if profile else "",
    )
    presence_minutes = gr.Number(
        label="Desk break after (minutes)",
        value=break_minutes,
        precision=0,
    )
    desk_break_preview = gr.Markdown(
        f"**Desk break message (auto):** _{break_preview}_"
    )
    gr.Markdown("### Notification TTS")
    tts_hydration = gr.Checkbox(label="Speak hydration reminders", value=tts_prefs.hydration)
    tts_exercise = gr.Checkbox(label="Speak exercise reminders (incl. 10-min heads-up)", value=tts_prefs.exercise)
    tts_medication = gr.Checkbox(label="Speak medication reminders", value=tts_prefs.medication)
    tts_meal = gr.Checkbox(label="Speak meal reminders", value=tts_prefs.meal)
    save_status = gr.Markdown("")
    save_btn = gr.Button("Save settings", variant="primary")
    regen_btn = gr.Button("Regenerate today's schedule")
    schedule_preview = gr.Markdown("")

    def update_break_preview(mins: float | None, name_value: str) -> str:
        """Refresh the desk break message preview."""
        display_name = name_value.strip() or "there"
        # The Number field reports None while the user is clearing/typing.
        minutes = int(mins) if mins is not None else 0
        message = build_desk_break_message(display_name, minutes)
        return f"**Desk break message (auto):** _{message}_"

    presence_minutes.change(
        update_break_preview,
        inputs=[presence_minutes, name],
        outputs=[desk_break_preview],
    )
    name.change(
        update_break_preview,
        inputs=[presence_minutes, name],
        outputs=[desk_break_preview],
    )

    def save_settings(
        name_value: str,
        wake_value: str,
        sleep_value: str,
        hydration_value: float | None,
        meds_value: str,
        presence_mins: float | None,
        hydration_tts: bool,
        exercise_tts: bool,
        medication_tts: bool,
        meal_tts: bool,
    ) -> str:
        """Persist profile, presence, and TTS settings, then apply them live."""
        logger.info("[settings] Save requested.")
        current = queries.get_profile()
        if current is None:
            logger.warning("[settings] Save failed: no profile found.")
            return "**Error:** No profile found."

        wake_clean = wake_value.strip()
        sleep_clean = sleep_value.strip()
        if not _TIME_INPUT_PATTERN.match(wake_clean):
            logger.warning("[settings] Save failed: invalid wake time %r.", wake_clean)
            return f"**Error:** Invalid wake time '{wake_clean}'. Use HH:MM (e.g. 07:00)."
        if not _TIME_INPUT_PATTERN.match(sleep_clean):
            logger.warning("[settings] Save failed: invalid sleep time %r.", sleep_clean)
            return f"**Error:** Invalid sleep time '{sleep_clean}'. Use HH:MM (e.g. 00:00)."

        if hydration_value is None or hydration_value <= 0:
            logger.warning("[settings] Save failed: invalid hydration goal %r.", hydration_value)
            return "**Error:** Hydration goal must be a positive number of litres."
        if presence_mins is None:
            logger.warning("[settings] Save failed: empty desk-break minutes.")
            return "**Error:** Desk break minutes is required (minimum 5)."

        try:
            medications = _parse_medications(meds_value)
        except ValueError as error:
            logger.warning("[settings] Save failed: %s", error)
            return f"**Error:** {error}"

        updated = ProfileInput(
            name=name_value.strip(),
            age=current.age,
            city=current.city,
            profession=current.profession,
            goal=current.goal,
            conditions=current.conditions,
            medications=medications,
            triggers=current.triggers,
            wake_time=wake_clean,
            sleep_time=sleep_clean,
            desk_worker=current.desk_worker,
            exercise_level=current.exercise_level,
            dietary_notes=current.dietary_notes,
            local_foods=current.local_foods,
        )
        queries.save_profile(updated)
        queries.save_hydration_goal_liters(float(hydration_value))
        queries.save_tts_preferences(
            TtsPreferences(
                hydration=hydration_tts,
                exercise=exercise_tts,
                medication=medication_tts,
                meal=meal_tts,
                check_in=exercise_tts,
            )
        )

        break_message = build_desk_break_message(updated.name, max(5, int(presence_mins)))
        presence_config = PresenceConfig(
            enabled=True,
            max_continuous_minutes=max(5, int(presence_mins)),
            break_message=break_message,
            break_duration_minutes=5,
            check_interval_seconds=300,
        )
        queries.save_presence_config(presence_config)

        job_count = reload_scheduler_jobs()
        restart_presence()
        logger.info(
            "[settings] Saved: wake=%s sleep=%s hydration=%sL meds=%s "
            "presence=%smin tts(h/e/m/meal)=%s/%s/%s/%s — scheduler reloaded %s jobs.",
            wake_clean,
            sleep_clean,
            hydration_value,
            len(medications),
            int(presence_mins),
            hydration_tts,
            exercise_tts,
            medication_tts,
            meal_tts,
            job_count,
        )
        return (
            "**Settings saved and applied.** Scheduler reloaded "
            f"({job_count} jobs) and presence check restarted. "
            "Use **Regenerate today's schedule** if you changed wake/sleep times."
        )

    def regenerate_schedule() -> str:
        """Force-regenerate today's LLM daily schedule."""
        logger.info("[settings] Regenerating today's schedule (forced).")
        queries.clear_morning_briefing_cache()
        ensure_today_schedule(force=True)
        plan = queries.get_daily_plan(date.today())
        if plan is None:
            return "_No schedule generated yet (before wake time?)._"
        lines = [f"### Today's schedule\n{plan.summary}\n"]
        for job in plan.jobs:
            extra = ""
            if job.volume_ml:
                extra = f" ({job.volume_ml}ml)"
            if job.exercise_type:
                extra = f" — {job.exercise_type}, {job.duration_minutes} min"
            tts_flag = "TTS" if job.tts else "silent"
            lines.append(f"- **{job.time}** [{job.type}/{tts_flag}] {job.message}{extra}")
        return "\n".join(lines)

    save_btn.click(
        save_settings,
        inputs=[
            name,
            wake_time,
            sleep_time,
            hydration_liters,
            medications_text,
            presence_minutes,
            tts_hydration,
            tts_exercise,
            tts_medication,
            tts_meal,
        ],
        outputs=[save_status],
    )
    regen_btn.click(regenerate_schedule, outputs=[schedule_preview])

    return {
        "name": name,
        "wake_time": wake_time,
        "sleep_time": sleep_time,
        "hydration_liters": hydration_liters,
        "medications_text": medications_text,
        "presence_minutes": presence_minutes,
        "desk_break_preview": desk_break_preview,
        "tts_hydration": tts_hydration,
        "tts_exercise": tts_exercise,
        "tts_medication": tts_medication,
        "tts_meal": tts_meal,
        "save_status": save_status,
    }
