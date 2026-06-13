"""SQLite table definitions for Vitál."""

PROFILE_TABLE = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    age INTEGER,
    city TEXT,
    profession TEXT,
    goal TEXT,
    conditions TEXT,
    medications TEXT,
    triggers TEXT,
    wake_time TEXT,
    sleep_time TEXT,
    desk_worker INTEGER,
    exercise_level TEXT,
    dietary_notes TEXT,
    local_foods TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

DAILY_LOG_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS daily_log_schema (
    id INTEGER PRIMARY KEY,
    field_id TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    type TEXT NOT NULL,
    options TEXT,
    display_order INTEGER NOT NULL,
    reason TEXT
);
"""

DAILY_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    field_id TEXT NOT NULL,
    value TEXT NOT NULL,
    logged_at TEXT NOT NULL
);
"""

MEDICATION_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS medication_log (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    medication_name TEXT NOT NULL,
    dose TEXT,
    scheduled_time TEXT NOT NULL,
    taken INTEGER DEFAULT 0,
    taken_at TEXT
);
"""

FOOD_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS food_log (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    food_description TEXT NOT NULL,
    llm_notes TEXT,
    logged_at TEXT NOT NULL
);
"""

EXERCISE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS exercise_log (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    exercise_type TEXT NOT NULL,
    duration_minutes INTEGER,
    completed INTEGER DEFAULT 0,
    notes TEXT,
    logged_at TEXT NOT NULL
);
"""

SCHEDULED_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY,
    job_id TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    time TEXT,
    interval_minutes INTEGER,
    days TEXT,
    message TEXT NOT NULL,
    tts INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    context TEXT
);
"""

MEAL_PLAN_TABLE = """
CREATE TABLE IF NOT EXISTS meal_plan (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    day_of_week TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    nutrients_focus TEXT,
    generated_at TEXT NOT NULL
);
"""

WEEKLY_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS weekly_reports (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    report_text TEXT NOT NULL,
    avg_pain REAL,
    water_goals_hit INTEGER,
    medication_adherence REAL,
    exercises_completed INTEGER,
    generated_at TEXT NOT NULL
);
"""

NOTIFICATIONS_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS notifications_log (
    id INTEGER PRIMARY KEY,
    job_id TEXT,
    message TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    tts_spoken INTEGER DEFAULT 0
);
"""

PRESENCE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS presence_log (
    id INTEGER PRIMARY KEY,
    detected INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    continuous_minutes INTEGER DEFAULT 0
);
"""

# Stores plan metadata not covered by the 11 PRD tables (presence config, system prompt, etc.).
SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DAILY_PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY,
    plan_date TEXT UNIQUE NOT NULL,
    summary TEXT NOT NULL,
    hydration_goal_liters REAL NOT NULL,
    generated_at TEXT NOT NULL
);
"""

DAILY_SCHEDULE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_schedule_jobs (
    id INTEGER PRIMARY KEY,
    plan_date TEXT NOT NULL,
    job_id TEXT NOT NULL,
    type TEXT NOT NULL,
    time TEXT NOT NULL,
    message TEXT NOT NULL,
    tts INTEGER DEFAULT 1,
    context TEXT,
    volume_ml INTEGER,
    exercise_type TEXT,
    duration_minutes INTEGER,
    UNIQUE(plan_date, job_id)
);
"""

ALL_TABLES: list[str] = [
    PROFILE_TABLE,
    DAILY_LOG_SCHEMA_TABLE,
    DAILY_LOGS_TABLE,
    MEDICATION_LOG_TABLE,
    FOOD_LOG_TABLE,
    EXERCISE_LOG_TABLE,
    SCHEDULED_JOBS_TABLE,
    MEAL_PLAN_TABLE,
    WEEKLY_REPORTS_TABLE,
    NOTIFICATIONS_LOG_TABLE,
    PRESENCE_LOG_TABLE,
    SETTINGS_TABLE,
    DAILY_PLANS_TABLE,
    DAILY_SCHEDULE_JOBS_TABLE,
]
