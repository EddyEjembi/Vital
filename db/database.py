"""SQLite connection management and schema initialisation."""

import sqlite3
import threading
from pathlib import Path

from db.models import ALL_TABLES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "vital.db"

_connection: sqlite3.Connection | None = None
_write_lock = threading.Lock()
_db_path: Path = DEFAULT_DB_PATH


def get_db_path() -> Path:
    """Return the active database file path."""
    return _db_path


def set_db_path(path: Path) -> None:
    """Override the database path (used by tests)."""
    global _db_path, _connection
    close_connection()
    _db_path = path


def get_connection() -> sqlite3.Connection:
    """Return the shared SQLite connection, creating it if needed."""
    global _connection
    if _connection is None:
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(
            str(_db_path),
            check_same_thread=False,
        )
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON;")
    return _connection


def close_connection() -> None:
    """Close the shared connection so a new path can be used."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


def _run_migrations(connection: sqlite3.Connection) -> None:
    """Apply lightweight schema migrations for existing databases."""
    profile_columns = connection.execute("PRAGMA table_info(profile);").fetchall()
    column_names = {row["name"] for row in profile_columns}
    if "profession" not in column_names:
        connection.execute("ALTER TABLE profile ADD COLUMN profession TEXT DEFAULT '';")


def initialize_database() -> None:
    """Create all tables if they do not already exist."""
    connection = get_connection()
    with _write_lock:
        for table_sql in ALL_TABLES:
            connection.execute(table_sql)
        _run_migrations(connection)
        connection.commit()


def reset_database() -> None:
    """Drop and recreate all tables (test helper only)."""
    connection = get_connection()
    with _write_lock:
        connection.execute("PRAGMA foreign_keys = OFF;")
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
        )
        table_names = [row["name"] for row in cursor.fetchall()]
        for table_name in table_names:
            connection.execute(f"DROP TABLE IF EXISTS {table_name};")
        connection.execute("PRAGMA foreign_keys = ON;")
        for table_sql in ALL_TABLES:
            connection.execute(table_sql)
        _run_migrations(connection)
        connection.commit()
