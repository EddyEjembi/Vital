"""All database read/write operations for Vitál."""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from db.database import _write_lock, get_connection
from vital_types.db import (
    DailyLogEntry,
    DailyLogSchemaField,
    ExerciseLogEntry,
    FoodLogEntry,
    MealPlanEntry,
    MedicationLogEntry,
    MedicationRecord,
    NotificationLogEntry,
    PresenceConfig,
    PresenceLogEntry,
    ProfileInput,
    ProfileRecord,
    ScheduledJob,
    ScheduledJobType,
    WeeklyReport,
)
from vital_types.daily_plan import DailyPlan, DailyScheduleJob
from vital_types.onboarding import OnboardingCommitData
from vital_types.settings_prefs import TtsPreferences

SETTING_ONBOARDING_COMPLETE = "onboarding_complete"
SETTING_HYDRATION_GOAL_LITERS = "hydration_goal_liters"
SETTING_TTS_PREFERENCES = "tts_preferences"
SETTING_PRESENCE_CONFIG = "presence_config"
SETTING_SYSTEM_PROMPT_ADDITIONS = "system_prompt_additions"
SETTING_MEAL_PLAN_FRAMEWORK = "meal_plan_framework"
SETTING_EXERCISE_PLAN = "exercise_plan"
SETTING_WEEKLY_CHECK_STRUCTURE = "weekly_check_structure"
SETTING_LAST_GREETED_DATE = "last_greeted_date"
SETTING_COACH_QUICK_QUESTIONS = "coach_quick_questions"
SETTING_MORNING_BRIEFING = "morning_briefing_cache"


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    """Serialize a datetime for SQLite storage."""
    return value.isoformat()


def _parse_datetime(value: str) -> datetime:
    """Parse a datetime string from SQLite."""
    return datetime.fromisoformat(value)


def _format_date(value: date) -> str:
    """Serialize a date for SQLite storage."""
    return value.isoformat()


def _parse_date(value: str) -> date:
    """Parse a date string from SQLite."""
    return date.fromisoformat(value)


def _bool_to_int(value: bool) -> int:
    """Convert a boolean to a SQLite integer."""
    return 1 if value else 0


def _int_to_bool(value: int | None) -> bool:
    """Convert a SQLite integer to a boolean."""
    return bool(value)


def _medications_to_json(medications: list[MedicationRecord]) -> str:
    """Serialize medication records for profile storage."""
    payload = [
        {"name": item.name, "dose": item.dose, "time": item.time}
        for item in medications
    ]
    return json.dumps(payload)


def _medications_from_json(raw_value: str | None) -> list[MedicationRecord]:
    """Deserialize medication records from profile storage."""
    if not raw_value:
        return []
    payload = json.loads(raw_value)
    return [
        MedicationRecord(
            name=item["name"],
            dose=item["dose"],
            time=item["time"],
        )
        for item in payload
    ]


def _profile_row_to_record(row: Any) -> ProfileRecord:
    """Map a profile SQLite row to a ProfileRecord."""
    return ProfileRecord(
        id=row["id"],
        name=row["name"],
        age=row["age"],
        city=row["city"],
        profession=row["profession"] or "",
        goal=row["goal"],
        conditions=json.loads(row["conditions"] or "[]"),
        medications=_medications_from_json(row["medications"]),
        triggers=json.loads(row["triggers"] or "[]"),
        wake_time=row["wake_time"],
        sleep_time=row["sleep_time"],
        desk_worker=_int_to_bool(row["desk_worker"]),
        exercise_level=row["exercise_level"],
        dietary_notes=row["dietary_notes"] or "",
        local_foods=row["local_foods"] or "",
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def save_profile(profile: ProfileInput) -> int:
    """Insert or replace the single user profile row."""
    now = _format_datetime(_utc_now())
    connection = get_connection()
    with _write_lock:
        existing = connection.execute("SELECT id FROM profile LIMIT 1;").fetchone()
        if existing:
            connection.execute(
                """
                UPDATE profile SET
                    name = ?, age = ?, city = ?, profession = ?, goal = ?,
                    conditions = ?, medications = ?, triggers = ?,
                    wake_time = ?, sleep_time = ?, desk_worker = ?,
                    exercise_level = ?, dietary_notes = ?, local_foods = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    profile.name,
                    profile.age,
                    profile.city,
                    profile.profession,
                    profile.goal,
                    json.dumps(profile.conditions),
                    _medications_to_json(profile.medications),
                    json.dumps(profile.triggers),
                    profile.wake_time,
                    profile.sleep_time,
                    _bool_to_int(profile.desk_worker),
                    profile.exercise_level,
                    profile.dietary_notes,
                    profile.local_foods,
                    now,
                    existing["id"],
                ),
            )
            connection.commit()
            return int(existing["id"])

        cursor = connection.execute(
            """
            INSERT INTO profile (
                name, age, city, profession, goal, conditions, medications, triggers,
                wake_time, sleep_time, desk_worker, exercise_level,
                dietary_notes, local_foods, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                profile.name,
                profile.age,
                profile.city,
                profile.profession,
                profile.goal,
                json.dumps(profile.conditions),
                _medications_to_json(profile.medications),
                json.dumps(profile.triggers),
                profile.wake_time,
                profile.sleep_time,
                _bool_to_int(profile.desk_worker),
                profile.exercise_level,
                profile.dietary_notes,
                profile.local_foods,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_profile() -> ProfileRecord | None:
    """Return the user profile if one exists."""
    connection = get_connection()
    row = connection.execute("SELECT * FROM profile LIMIT 1;").fetchone()
    if row is None:
        return None
    return _profile_row_to_record(row)


def set_setting(key: str, value: str) -> None:
    """Upsert a key-value application setting."""
    connection = get_connection()
    with _write_lock:
        connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (key, value),
        )
        connection.commit()


def get_setting(key: str) -> str | None:
    """Read a single application setting value."""
    connection = get_connection()
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?;",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return row["value"]


def set_onboarding_complete(complete: bool) -> None:
    """Mark whether onboarding has finished."""
    set_setting(SETTING_ONBOARDING_COMPLETE, "true" if complete else "false")


def check_onboarding_status() -> bool:
    """Return true when a valid onboarded profile exists."""
    profile = get_profile()
    if profile is None or not profile.name:
        return False
    onboarding_flag = get_setting(SETTING_ONBOARDING_COMPLETE)
    return onboarding_flag == "true"


def save_presence_config(config: PresenceConfig) -> None:
    """Persist the user-approved presence configuration."""
    payload = {
        "enabled": config.enabled,
        "max_continuous_minutes": config.max_continuous_minutes,
        "break_message": config.break_message,
        "break_duration_minutes": config.break_duration_minutes,
        "check_interval_seconds": config.check_interval_seconds,
    }
    set_setting(SETTING_PRESENCE_CONFIG, json.dumps(payload))


def get_presence_config() -> PresenceConfig | None:
    """Load the presence configuration from settings."""
    raw_value = get_setting(SETTING_PRESENCE_CONFIG)
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    return PresenceConfig(
        enabled=payload["enabled"],
        max_continuous_minutes=payload["max_continuous_minutes"],
        break_message=payload["break_message"],
        break_duration_minutes=payload["break_duration_minutes"],
        check_interval_seconds=payload.get("check_interval_seconds", 300),
    )


def get_presence_break_message() -> str:
    """Return the desk-break notification message."""
    config = get_presence_config()
    if config is None:
        return "You have been at your desk for a while. Stand up, stretch, and drink water."
    return config.break_message


def save_system_prompt_additions(text: str) -> None:
    """Store LLM-generated condition-specific prompt additions."""
    set_setting(SETTING_SYSTEM_PROMPT_ADDITIONS, text)


def get_system_prompt_additions() -> str:
    """Read condition-specific prompt additions."""
    return get_setting(SETTING_SYSTEM_PROMPT_ADDITIONS) or ""


def replace_daily_log_schema(fields: list[DailyLogSchemaField]) -> None:
    """Replace all daily check-in field definitions."""
    connection = get_connection()
    with _write_lock:
        connection.execute("DELETE FROM daily_log_schema;")
        for field in fields:
            connection.execute(
                """
                INSERT INTO daily_log_schema (
                    field_id, label, type, options, display_order, reason
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    field.field_id,
                    field.label,
                    field.type,
                    json.dumps(field.options),
                    field.display_order,
                    field.reason,
                ),
            )
        connection.commit()


def get_daily_log_schema() -> list[DailyLogSchemaField]:
    """Return all daily check-in field definitions in display order."""
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT * FROM daily_log_schema
        ORDER BY display_order ASC, id ASC;
        """
    ).fetchall()
    return [
        DailyLogSchemaField(
            id=row["id"],
            field_id=row["field_id"],
            label=row["label"],
            type=row["type"],
            options=json.loads(row["options"] or "[]"),
            display_order=row["display_order"],
            reason=row["reason"] or "",
        )
        for row in rows
    ]


def upsert_daily_log(entry_date: date, field_id: str, value: str) -> None:
    """Insert or update one daily check-in value for a date."""
    connection = get_connection()
    now = _format_datetime(_utc_now())
    date_value = _format_date(entry_date)
    with _write_lock:
        existing = connection.execute(
            """
            SELECT id FROM daily_logs
            WHERE date = ? AND field_id = ?;
            """,
            (date_value, field_id),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE daily_logs
                SET value = ?, logged_at = ?
                WHERE id = ?;
                """,
                (value, now, existing["id"]),
            )
        else:
            connection.execute(
                """
                INSERT INTO daily_logs (date, field_id, value, logged_at)
                VALUES (?, ?, ?, ?);
                """,
                (date_value, field_id, value, now),
            )
        connection.commit()


def get_daily_logs_for_date(entry_date: date) -> list[DailyLogEntry]:
    """Return all daily check-in values for a given date."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM daily_logs WHERE date = ? ORDER BY id ASC;",
        (_format_date(entry_date),),
    ).fetchall()
    return [
        DailyLogEntry(
            id=row["id"],
            date=_parse_date(row["date"]),
            field_id=row["field_id"],
            value=row["value"],
            logged_at=_parse_datetime(row["logged_at"]),
        )
        for row in rows
    ]


def insert_medication_log(entry: MedicationLogEntry) -> int:
    """Insert a medication log row."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO medication_log (
                date, medication_name, dose, scheduled_time, taken, taken_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                _format_date(entry.date),
                entry.medication_name,
                entry.dose,
                entry.scheduled_time,
                _bool_to_int(entry.taken),
                _format_datetime(entry.taken_at) if entry.taken_at else None,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_medications_for_date(entry_date: date) -> list[MedicationLogEntry]:
    """Return medication doses scheduled for a date."""
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT * FROM medication_log
        WHERE date = ?
        ORDER BY scheduled_time ASC, id ASC;
        """,
        (_format_date(entry_date),),
    ).fetchall()
    return [
        MedicationLogEntry(
            id=row["id"],
            date=_parse_date(row["date"]),
            medication_name=row["medication_name"],
            dose=row["dose"] or "",
            scheduled_time=row["scheduled_time"],
            taken=_int_to_bool(row["taken"]),
            taken_at=_parse_datetime(row["taken_at"]) if row["taken_at"] else None,
        )
        for row in rows
    ]


def mark_medication_taken(
    entry_date: date,
    medication_name: str,
    scheduled_time: str,
) -> bool:
    """Mark a scheduled medication dose as taken."""
    return set_medication_taken_status(
        entry_date,
        medication_name,
        scheduled_time,
        taken=True,
    )


def set_medication_taken_status(
    entry_date: date,
    medication_name: str,
    scheduled_time: str,
    taken: bool,
) -> bool:
    """Set whether a scheduled medication dose was taken."""
    connection = get_connection()
    taken_at = _format_datetime(_utc_now()) if taken else None
    with _write_lock:
        cursor = connection.execute(
            """
            UPDATE medication_log
            SET taken = ?, taken_at = ?
            WHERE date = ? AND medication_name = ? AND scheduled_time = ?;
            """,
            (
                _bool_to_int(taken),
                taken_at,
                _format_date(entry_date),
                medication_name,
                scheduled_time,
            ),
        )
        connection.commit()
        return cursor.rowcount > 0


def insert_food_log(entry: FoodLogEntry) -> int:
    """Insert a food log row."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO food_log (
                date, meal_type, food_description, llm_notes, logged_at
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (
                _format_date(entry.date),
                entry.meal_type,
                entry.food_description,
                entry.llm_notes,
                _format_datetime(entry.logged_at),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_food_logs_for_date(entry_date: date) -> list[FoodLogEntry]:
    """Return food log entries for a date."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM food_log WHERE date = ? ORDER BY logged_at ASC;",
        (_format_date(entry_date),),
    ).fetchall()
    return [
        FoodLogEntry(
            id=row["id"],
            date=_parse_date(row["date"]),
            meal_type=row["meal_type"],
            food_description=row["food_description"],
            llm_notes=row["llm_notes"],
            logged_at=_parse_datetime(row["logged_at"]),
        )
        for row in rows
    ]


def insert_exercise_log(entry: ExerciseLogEntry) -> int:
    """Insert an exercise log row."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO exercise_log (
                date, exercise_type, duration_minutes, completed, notes, logged_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                _format_date(entry.date),
                entry.exercise_type,
                entry.duration_minutes,
                _bool_to_int(entry.completed),
                entry.notes,
                _format_datetime(entry.logged_at),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_exercise_logs_for_date(entry_date: date) -> list[ExerciseLogEntry]:
    """Return exercise log entries for a date."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT * FROM exercise_log WHERE date = ? ORDER BY logged_at ASC;",
        (_format_date(entry_date),),
    ).fetchall()
    return [
        ExerciseLogEntry(
            id=row["id"],
            date=_parse_date(row["date"]),
            exercise_type=row["exercise_type"],
            duration_minutes=row["duration_minutes"] or 0,
            completed=_int_to_bool(row["completed"]),
            notes=row["notes"],
            logged_at=_parse_datetime(row["logged_at"]),
        )
        for row in rows
    ]


def replace_scheduled_jobs(jobs: list[ScheduledJob]) -> None:
    """Replace all scheduled jobs with a new set."""
    connection = get_connection()
    with _write_lock:
        connection.execute("DELETE FROM scheduled_jobs;")
        for job in jobs:
            connection.execute(
                """
                INSERT INTO scheduled_jobs (
                    job_id, type, schedule_type, time, interval_minutes,
                    days, message, tts, active, context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    job.job_id,
                    job.type,
                    job.schedule_type,
                    job.time,
                    job.interval_minutes,
                    job.days,
                    job.message,
                    _bool_to_int(job.tts),
                    _bool_to_int(job.active),
                    job.context,
                ),
            )
        connection.commit()


def get_all_scheduled_jobs(active_only: bool = True) -> list[ScheduledJob]:
    """Return scheduled jobs, optionally filtering to active rows only."""
    connection = get_connection()
    if active_only:
        rows = connection.execute(
            "SELECT * FROM scheduled_jobs WHERE active = 1 ORDER BY id ASC;"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM scheduled_jobs ORDER BY id ASC;"
        ).fetchall()
    return [
        ScheduledJob(
            id=row["id"],
            job_id=row["job_id"],
            type=row["type"],
            schedule_type=row["schedule_type"],
            time=row["time"],
            interval_minutes=row["interval_minutes"],
            days=row["days"],
            message=row["message"],
            tts=_int_to_bool(row["tts"]),
            active=_int_to_bool(row["active"]),
            context=row["context"] or "",
        )
        for row in rows
    ]


def insert_meal_plan_entries(entries: list[MealPlanEntry]) -> None:
    """Insert meal plan rows."""
    connection = get_connection()
    with _write_lock:
        for entry in entries:
            connection.execute(
                """
                INSERT INTO meal_plan (
                    week_start, day_of_week, meal_type, suggestion,
                    nutrients_focus, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    _format_date(entry.week_start),
                    entry.day_of_week,
                    entry.meal_type,
                    entry.suggestion,
                    entry.nutrients_focus,
                    _format_datetime(entry.generated_at),
                ),
            )
        connection.commit()


def get_meal_plan_for_week(week_start: date) -> list[MealPlanEntry]:
    """Return meal plan entries for a week."""
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT * FROM meal_plan
        WHERE week_start = ?
        ORDER BY id ASC;
        """,
        (_format_date(week_start),),
    ).fetchall()
    return [
        MealPlanEntry(
            id=row["id"],
            week_start=_parse_date(row["week_start"]),
            day_of_week=row["day_of_week"],
            meal_type=row["meal_type"],
            suggestion=row["suggestion"],
            nutrients_focus=row["nutrients_focus"] or "",
            generated_at=_parse_datetime(row["generated_at"]),
        )
        for row in rows
    ]


def insert_weekly_report(report: WeeklyReport) -> int:
    """Insert a weekly report row."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO weekly_reports (
                week_start, report_text, avg_pain, water_goals_hit,
                medication_adherence, exercises_completed, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                _format_date(report.week_start),
                report.report_text,
                report.avg_pain,
                report.water_goals_hit,
                report.medication_adherence,
                report.exercises_completed,
                _format_datetime(report.generated_at),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_latest_weekly_report() -> WeeklyReport | None:
    """Return the most recently generated weekly report."""
    connection = get_connection()
    row = connection.execute(
        "SELECT * FROM weekly_reports ORDER BY generated_at DESC LIMIT 1;"
    ).fetchone()
    if row is None:
        return None
    return WeeklyReport(
        id=row["id"],
        week_start=_parse_date(row["week_start"]),
        report_text=row["report_text"],
        avg_pain=row["avg_pain"],
        water_goals_hit=row["water_goals_hit"] or 0,
        medication_adherence=row["medication_adherence"] or 0.0,
        exercises_completed=row["exercises_completed"] or 0,
        generated_at=_parse_datetime(row["generated_at"]),
    )


def delete_weekly_report(week_start: date) -> None:
    """Delete the weekly report for a given week start date."""
    connection = get_connection()
    with _write_lock:
        connection.execute(
            "DELETE FROM weekly_reports WHERE week_start = ?;",
            (_format_date(week_start),),
        )
        connection.commit()


def get_weekly_report(week_start: date) -> WeeklyReport | None:
    """Return the weekly report for a given week start date."""
    connection = get_connection()
    row = connection.execute(
        "SELECT * FROM weekly_reports WHERE week_start = ? LIMIT 1;",
        (_format_date(week_start),),
    ).fetchone()
    if row is None:
        return None
    return WeeklyReport(
        id=row["id"],
        week_start=_parse_date(row["week_start"]),
        report_text=row["report_text"],
        avg_pain=row["avg_pain"],
        water_goals_hit=row["water_goals_hit"] or 0,
        medication_adherence=row["medication_adherence"] or 0.0,
        exercises_completed=row["exercises_completed"] or 0,
        generated_at=_parse_datetime(row["generated_at"]),
    )


def insert_notification_log(entry: NotificationLogEntry) -> int:
    """Insert a delivered notification record."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO notifications_log (job_id, message, delivered_at, tts_spoken)
            VALUES (?, ?, ?, ?);
            """,
            (
                entry.job_id,
                entry.message,
                _format_datetime(entry.delivered_at),
                _bool_to_int(entry.tts_spoken),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def insert_presence_log(entry: PresenceLogEntry) -> int:
    """Insert a presence detection check result."""
    connection = get_connection()
    with _write_lock:
        cursor = connection.execute(
            """
            INSERT INTO presence_log (detected, checked_at, continuous_minutes)
            VALUES (?, ?, ?);
            """,
            (
                _bool_to_int(entry.detected),
                _format_datetime(entry.checked_at),
                entry.continuous_minutes,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def already_greeted_today() -> bool:
    """Return true if the morning greeting already ran today."""
    last_greeted = get_setting(SETTING_LAST_GREETED_DATE)
    if not last_greeted:
        return False
    return last_greeted == _format_date(date.today())


def mark_greeted_today() -> None:
    """Record that today's morning greeting has been delivered."""
    set_setting(SETTING_LAST_GREETED_DATE, _format_date(date.today()))


def _monday_of_week(entry_date: date) -> date:
    """Return the Monday that starts the week containing entry_date."""
    return entry_date - timedelta(days=entry_date.weekday())


def monday_of_week(entry_date: date) -> date:
    """Return the Monday that starts the week containing entry_date."""
    return _monday_of_week(entry_date)


def get_food_logs_between(
    start_date: date,
    end_date: date,
) -> list[FoodLogEntry]:
    """Return food log entries for each day from start_date through end_date inclusive."""
    if end_date < start_date:
        return []

    entries: list[FoodLogEntry] = []
    current = start_date
    while current <= end_date:
        entries.extend(get_food_logs_for_date(current))
        current += timedelta(days=1)
    return entries


def get_meal_plan_for_date(entry_date: date | None = None) -> list[MealPlanEntry]:
    """Return meal plan entries for a specific calendar date."""
    target_date = entry_date or date.today()
    week_start = _monday_of_week(target_date)
    day_name = target_date.strftime("%A")
    week_entries = get_meal_plan_for_week(week_start)
    return [entry for entry in week_entries if entry.day_of_week == day_name]


def get_recent_logs(days: int = 7) -> list[DailyLogEntry]:
    """Return daily check-in logs for the last N days."""
    if days < 1:
        days = 1
    if days > 30:
        days = 30

    connection = get_connection()
    start_date = date.today() - timedelta(days=days - 1)
    rows = connection.execute(
        """
        SELECT * FROM daily_logs
        WHERE date >= ?
        ORDER BY date ASC, id ASC;
        """,
        (_format_date(start_date),),
    ).fetchall()
    return [
        DailyLogEntry(
            id=row["id"],
            date=_parse_date(row["date"]),
            field_id=row["field_id"],
            value=row["value"],
            logged_at=_parse_datetime(row["logged_at"]),
        )
        for row in rows
    ]


def get_weekly_summary_for_week(week_start: date) -> dict[str, object]:
    """Aggregate wellness stats for a specific Monday-based week."""
    week_end = week_start + timedelta(days=6)

    medications_taken = 0
    medications_total = 0
    exercises_completed = 0
    food_entries = 0
    daily_log_count = 0

    current = week_start
    while current <= week_end:
        for med in get_medications_for_date(current):
            medications_total += 1
            if med.taken:
                medications_taken += 1
        for exercise in get_exercise_logs_for_date(current):
            if exercise.completed:
                exercises_completed += 1
        food_entries += len(get_food_logs_for_date(current))
        daily_log_count += len(get_daily_logs_for_date(current))
        current += timedelta(days=1)

    adherence = 0.0
    if medications_total > 0:
        adherence = round((medications_taken / medications_total) * 100, 1)

    return {
        "week_start": _format_date(week_start),
        "week_end": _format_date(week_end),
        "medications_taken": medications_taken,
        "medications_total": medications_total,
        "medication_adherence_percent": adherence,
        "exercises_completed": exercises_completed,
        "food_entries": food_entries,
        "daily_log_entries": daily_log_count,
    }


def get_weekly_summary(week: str = "current") -> dict[str, object]:
    """Aggregate wellness stats for the current or previous week."""
    today = date.today()
    reference_date = today if week == "current" else today - timedelta(days=7)
    week_start = _monday_of_week(reference_date)
    return get_weekly_summary_for_week(week_start)


def log_water(cups: int, entry_date: date | None = None) -> None:
    """Update today's water cup count in daily logs."""
    target_date = entry_date or date.today()
    upsert_daily_log(target_date, "water_cups", str(cups))


def save_meal_plan_framework(data: dict[str, object]) -> None:
    """Persist the onboarding meal plan framework to settings."""
    set_setting(SETTING_MEAL_PLAN_FRAMEWORK, json.dumps(data))


def get_meal_plan_framework() -> dict[str, object] | None:
    """Load the meal plan framework from settings."""
    raw_value = get_setting(SETTING_MEAL_PLAN_FRAMEWORK)
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if isinstance(payload, dict):
        return payload
    return None


def save_exercise_plan(data: dict[str, object]) -> None:
    """Persist the onboarding exercise plan to settings."""
    set_setting(SETTING_EXERCISE_PLAN, json.dumps(data))


def get_exercise_plan() -> dict[str, object] | None:
    """Load the exercise plan from settings."""
    raw_value = get_setting(SETTING_EXERCISE_PLAN)
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if isinstance(payload, dict):
        return payload
    return None


def save_weekly_check_structure(data: dict[str, object]) -> None:
    """Persist the weekly report/replan schedule to settings."""
    set_setting(SETTING_WEEKLY_CHECK_STRUCTURE, json.dumps(data))


def get_weekly_check_structure() -> dict[str, object] | None:
    """Load the weekly report/replan schedule from settings."""
    raw_value = get_setting(SETTING_WEEKLY_CHECK_STRUCTURE)
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if isinstance(payload, dict):
        return payload
    return None


def save_coach_quick_questions(questions: list[str]) -> None:
    """Persist coach quick-question chips from onboarding."""
    set_setting(SETTING_COACH_QUICK_QUESTIONS, json.dumps(questions))


def get_coach_quick_questions() -> list[str]:
    """Load coach quick-question chips."""
    raw_value = get_setting(SETTING_COACH_QUICK_QUESTIONS)
    if not raw_value:
        return []
    payload = json.loads(raw_value)
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def save_hydration_goal_liters(liters: float) -> None:
    """Persist the user's daily hydration target in litres."""
    set_setting(SETTING_HYDRATION_GOAL_LITERS, str(round(max(0.5, liters), 2)))


def get_hydration_goal_liters() -> float:
    """Load the user's daily hydration target in litres."""
    raw_value = get_setting(SETTING_HYDRATION_GOAL_LITERS)
    if not raw_value:
        return 2.5
    try:
        return max(0.5, float(raw_value))
    except ValueError:
        return 2.5


def save_tts_preferences(preferences: TtsPreferences) -> None:
    """Persist per-category TTS notification toggles."""
    payload = {
        "hydration": preferences.hydration,
        "exercise": preferences.exercise,
        "medication": preferences.medication,
        "meal": preferences.meal,
        "check_in": preferences.check_in,
    }
    set_setting(SETTING_TTS_PREFERENCES, json.dumps(payload))


def get_tts_preferences() -> TtsPreferences:
    """Load per-category TTS notification toggles."""
    raw_value = get_setting(SETTING_TTS_PREFERENCES)
    if not raw_value:
        return TtsPreferences()
    payload = json.loads(raw_value)
    if not isinstance(payload, dict):
        return TtsPreferences()
    return TtsPreferences(
        hydration=bool(payload.get("hydration", True)),
        exercise=bool(payload.get("exercise", True)),
        medication=bool(payload.get("medication", True)),
        meal=bool(payload.get("meal", True)),
        check_in=bool(payload.get("check_in", True)),
    )


def tts_enabled_for_job_type(job_type: str) -> bool:
    """Return whether TTS is enabled for a scheduled job category."""
    preferences = get_tts_preferences()
    if job_type == "hydration":
        return preferences.hydration
    if job_type == "exercise":
        return preferences.exercise
    if job_type == "medication":
        return preferences.medication
    if job_type == "meal":
        return preferences.meal
    if job_type == "check_in":
        return preferences.check_in
    return True


def clear_all_daily_schedules() -> int:
    """Delete every row from daily plan tables; return rows removed."""
    connection = get_connection()
    with _write_lock:
        job_count = connection.execute("SELECT COUNT(*) AS c FROM daily_schedule_jobs;").fetchone()["c"]
        plan_count = connection.execute("SELECT COUNT(*) AS c FROM daily_plans;").fetchone()["c"]
        connection.execute("DELETE FROM daily_schedule_jobs;")
        connection.execute("DELETE FROM daily_plans;")
        connection.commit()
    return int(job_count) + int(plan_count)


def find_previous_daily_plan(before_date: date, max_lookback_days: int = 30) -> DailyPlan | None:
    """Return the most recent daily plan strictly before the given date."""
    for offset in range(1, max_lookback_days + 1):
        candidate = before_date - timedelta(days=offset)
        plan = get_daily_plan(candidate)
        if plan is not None:
            return plan
    return None


def has_daily_plan(plan_date: date) -> bool:
    """Return True when a daily plan exists for the given date."""
    connection = get_connection()
    row = connection.execute(
        "SELECT id FROM daily_plans WHERE plan_date = ? LIMIT 1;",
        (_format_date(plan_date),),
    ).fetchone()
    return row is not None


def get_daily_plan(plan_date: date) -> DailyPlan | None:
    """Load the full daily plan and its jobs for a date."""
    connection = get_connection()
    row = connection.execute(
        "SELECT * FROM daily_plans WHERE plan_date = ? LIMIT 1;",
        (_format_date(plan_date),),
    ).fetchone()
    if row is None:
        return None

    job_rows = connection.execute(
        "SELECT * FROM daily_schedule_jobs WHERE plan_date = ? ORDER BY time ASC;",
        (_format_date(plan_date),),
    ).fetchall()
    jobs = [
        DailyScheduleJob(
            id=job_row["id"],
            job_id=job_row["job_id"],
            type=cast(ScheduledJobType, job_row["type"]),
            time=job_row["time"],
            message=job_row["message"],
            tts=_int_to_bool(job_row["tts"]),
            context=job_row["context"] or "",
            volume_ml=job_row["volume_ml"],
            exercise_type=job_row["exercise_type"],
            duration_minutes=job_row["duration_minutes"],
        )
        for job_row in job_rows
    ]
    return DailyPlan(
        id=row["id"],
        plan_date=_parse_date(row["plan_date"]),
        summary=row["summary"],
        hydration_goal_liters=float(row["hydration_goal_liters"]),
        generated_at=_parse_datetime(row["generated_at"]),
        jobs=jobs,
    )


def delete_daily_plan(plan_date: date) -> None:
    """Remove a daily plan and its jobs for a date."""
    connection = get_connection()
    with _write_lock:
        connection.execute(
            "DELETE FROM daily_schedule_jobs WHERE plan_date = ?;",
            (_format_date(plan_date),),
        )
        connection.execute(
            "DELETE FROM daily_plans WHERE plan_date = ?;",
            (_format_date(plan_date),),
        )
        connection.commit()


def save_daily_plan(plan: DailyPlan) -> None:
    """Persist a daily plan and replace jobs for that date."""
    connection = get_connection()
    with _write_lock:
        connection.execute(
            "DELETE FROM daily_schedule_jobs WHERE plan_date = ?;",
            (_format_date(plan.plan_date),),
        )
        connection.execute(
            "DELETE FROM daily_plans WHERE plan_date = ?;",
            (_format_date(plan.plan_date),),
        )
        connection.execute(
            """
            INSERT INTO daily_plans (
                plan_date, summary, hydration_goal_liters, generated_at
            ) VALUES (?, ?, ?, ?);
            """,
            (
                _format_date(plan.plan_date),
                plan.summary,
                plan.hydration_goal_liters,
                _format_datetime(plan.generated_at),
            ),
        )
        for job in plan.jobs:
            connection.execute(
                """
                INSERT INTO daily_schedule_jobs (
                    plan_date, job_id, type, time, message, tts, context,
                    volume_ml, exercise_type, duration_minutes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    _format_date(plan.plan_date),
                    job.job_id,
                    job.type,
                    job.time,
                    job.message,
                    _bool_to_int(job.tts),
                    job.context,
                    job.volume_ml,
                    job.exercise_type,
                    job.duration_minutes,
                ),
            )
        connection.commit()


def build_medication_scheduler_jobs(profile: ProfileInput) -> list[ScheduledJob]:
    """Build daily medication reminder jobs from the user profile."""
    med_tts = tts_enabled_for_job_type("medication")
    jobs: list[ScheduledJob] = []
    for medication in profile.medications:
        safe_name = medication.name.lower().replace(" ", "_")
        safe_time = medication.time.replace(":", "")
        jobs.append(
            ScheduledJob(
                job_id=f"med_{safe_name}_{safe_time}",
                type="medication",
                schedule_type="daily_time",
                time=medication.time,
                interval_minutes=None,
                days="daily",
                message=f"Time for your {medication.name} ({medication.dose})",
                tts=med_tts,
                active=True,
                context="Daily medication from your profile",
            )
        )
    return jobs


def _daily_job_to_scheduled(job: DailyScheduleJob, plan_date: date) -> ScheduledJob:
    """Convert a daily schedule job into an APScheduler-compatible job.

    TTS is resolved from Settings at load/fire time (single source of truth),
    so toggling a category in Settings takes effect on the next reminder
    without regenerating the plan or restarting the app.
    """
    return ScheduledJob(
        job_id=f"daily_{plan_date.isoformat()}_{job.job_id}",
        type=job.type,
        schedule_type="daily_time",
        time=job.time,
        interval_minutes=None,
        days="daily",
        message=job.message,
        tts=tts_enabled_for_job_type(job.type),
        active=True,
        context=job.context,
    )


def get_combined_scheduler_jobs(plan_date: date | None = None) -> list[ScheduledJob]:
    """Merge medication jobs and today's LLM-generated daily schedule jobs."""
    target_date = plan_date or date.today()
    jobs: list[ScheduledJob] = []
    profile = get_profile()
    if profile is not None:
        jobs.extend(build_medication_scheduler_jobs(profile))

    daily_plan = get_daily_plan(target_date)
    if daily_plan is not None:
        for daily_job in daily_plan.jobs:
            jobs.append(_daily_job_to_scheduled(daily_job, target_date))
        return jobs

    legacy_jobs = get_all_scheduled_jobs(active_only=True)
    for legacy_job in legacy_jobs:
        if legacy_job.type == "medication":
            continue
        jobs.append(legacy_job)
    return jobs


def save_morning_briefing_cache(entry_date: date, text: str) -> None:
    """Cache today's morning briefing for the dashboard."""
    payload = {"date": _format_date(entry_date), "text": text}
    set_setting(SETTING_MORNING_BRIEFING, json.dumps(payload))


def clear_morning_briefing_cache() -> None:
    """Drop the cached briefing so the next dashboard load regenerates it."""
    set_setting(SETTING_MORNING_BRIEFING, "")


def get_morning_briefing_cache(entry_date: date | None = None) -> str | None:
    """Return the cached morning briefing for a date if present."""
    target_date = entry_date or date.today()
    raw_value = get_setting(SETTING_MORNING_BRIEFING)
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if not isinstance(payload, dict):
        return None
    if payload.get("date") != _format_date(target_date):
        return None
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def write_profile_cache() -> None:
    """Write profile.json cache after onboarding commit."""
    from core.app_config import PROJECT_ROOT

    profile = get_profile()
    if profile is None:
        return

    cache_path = PROJECT_ROOT / "data" / "profile.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": profile.name,
        "age": profile.age,
        "city": profile.city,
        "profession": profile.profession,
        "goal": profile.goal,
        "conditions": profile.conditions,
        "medications": [
            {"name": item.name, "dose": item.dose, "time": item.time}
            for item in profile.medications
        ],
        "triggers": profile.triggers,
        "wake_time": profile.wake_time,
        "sleep_time": profile.sleep_time,
        "desk_worker": profile.desk_worker,
        "exercise_level": profile.exercise_level,
        "dietary_notes": profile.dietary_notes,
        "local_foods": profile.local_foods,
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def commit_onboarding_plan(data: OnboardingCommitData) -> int:
    """Atomically write the approved onboarding plan to the database."""
    connection = get_connection()
    now = _format_datetime(_utc_now())
    profile = data.profile
    plan = data.plan

    with _write_lock:
        try:
            connection.execute("BEGIN IMMEDIATE;")

            existing = connection.execute("SELECT id FROM profile LIMIT 1;").fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE profile SET
                        name = ?, age = ?, city = ?, profession = ?, goal = ?,
                        conditions = ?, medications = ?, triggers = ?,
                        wake_time = ?, sleep_time = ?, desk_worker = ?,
                        exercise_level = ?, dietary_notes = ?, local_foods = ?,
                        updated_at = ?
                    WHERE id = ?;
                    """,
                    (
                        profile.name,
                        profile.age,
                        profile.city,
                        profile.profession,
                        profile.goal,
                        json.dumps(profile.conditions),
                        _medications_to_json(profile.medications),
                        json.dumps(profile.triggers),
                        profile.wake_time,
                        profile.sleep_time,
                        _bool_to_int(profile.desk_worker),
                        profile.exercise_level,
                        profile.dietary_notes,
                        profile.local_foods,
                        now,
                        existing["id"],
                    ),
                )
                profile_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO profile (
                        name, age, city, profession, goal, conditions, medications, triggers,
                        wake_time, sleep_time, desk_worker, exercise_level,
                        dietary_notes, local_foods, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        profile.name,
                        profile.age,
                        profile.city,
                        profile.profession,
                        profile.goal,
                        json.dumps(profile.conditions),
                        _medications_to_json(profile.medications),
                        json.dumps(profile.triggers),
                        profile.wake_time,
                        profile.sleep_time,
                        _bool_to_int(profile.desk_worker),
                        profile.exercise_level,
                        profile.dietary_notes,
                        profile.local_foods,
                        now,
                        now,
                    ),
                )
                profile_id = int(cursor.lastrowid)

            connection.execute("DELETE FROM daily_log_schema;")
            for field in plan.daily_log_fields:
                connection.execute(
                    """
                    INSERT INTO daily_log_schema (
                        field_id, label, type, options, display_order, reason
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        field.field_id,
                        field.label,
                        field.type,
                        json.dumps(field.options),
                        field.display_order,
                        field.reason,
                    ),
                )

            connection.execute("DELETE FROM scheduled_jobs;")
            for job in plan.scheduled_jobs:
                connection.execute(
                    """
                    INSERT INTO scheduled_jobs (
                        job_id, type, schedule_type, time, interval_minutes,
                        days, message, tts, active, context
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        job.job_id,
                        job.type,
                        job.schedule_type,
                        job.time,
                        job.interval_minutes,
                        job.days,
                        job.message,
                        _bool_to_int(job.tts),
                        _bool_to_int(job.active),
                        job.context,
                    ),
                )

            presence_payload = {
                "enabled": plan.presence_check.enabled,
                "max_continuous_minutes": plan.presence_check.max_continuous_minutes,
                "break_message": plan.presence_check.break_message,
                "break_duration_minutes": plan.presence_check.break_duration_minutes,
                "check_interval_seconds": plan.presence_check.check_interval_seconds,
            }
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (SETTING_PRESENCE_CONFIG, json.dumps(presence_payload)),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (SETTING_SYSTEM_PROMPT_ADDITIONS, plan.system_prompt_additions),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (
                    SETTING_MEAL_PLAN_FRAMEWORK,
                    json.dumps(
                        {
                            "nutrients_to_prioritise": plan.meal_plan_framework.nutrients_to_prioritise,
                            "nutrients_to_moderate": plan.meal_plan_framework.nutrients_to_moderate,
                            "meal_frequency": plan.meal_plan_framework.meal_frequency,
                            "notes": plan.meal_plan_framework.notes,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (
                    SETTING_EXERCISE_PLAN,
                    json.dumps(
                        {
                            "frequency": plan.exercise_plan.frequency,
                            "intensity": plan.exercise_plan.intensity,
                            "session_duration_minutes": plan.exercise_plan.session_duration_minutes,
                            "types": plan.exercise_plan.types,
                            "avoid": plan.exercise_plan.avoid,
                            "notes": plan.exercise_plan.notes,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (
                    SETTING_WEEKLY_CHECK_STRUCTURE,
                    json.dumps(
                        {
                            "report_day": plan.weekly_check_structure.report_day,
                            "report_time": plan.weekly_check_structure.report_time,
                            "replan_day": plan.weekly_check_structure.replan_day,
                            "replan_time": plan.weekly_check_structure.replan_time,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (SETTING_HYDRATION_GOAL_LITERS, str(plan.hydration_goal_liters)),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (SETTING_COACH_QUICK_QUESTIONS, json.dumps(plan.coach_quick_questions)),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (SETTING_ONBOARDING_COMPLETE, "true"),
            )

            connection.commit()
        except Exception:
            connection.rollback()
            raise

    write_profile_cache()
    return profile_id


def ensure_daily_rows(entry_date: date | None = None) -> None:
    """Seed today's medication_log rows from the profile schedule (idempotent)."""
    target_date = entry_date or date.today()
    profile = get_profile()
    if profile is None:
        return

    existing = get_medications_for_date(target_date)
    existing_keys = {(item.medication_name, item.scheduled_time) for item in existing}

    for medication in profile.medications:
        key = (medication.name, medication.time)
        if key in existing_keys:
            continue
        insert_medication_log(
            MedicationLogEntry(
                date=target_date,
                medication_name=medication.name,
                dose=medication.dose,
                scheduled_time=medication.time,
                taken=False,
                taken_at=None,
            )
        )
