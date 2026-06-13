"""
Manual presence detection test.

Runs for ~1 minute, checking the webcam every 10 seconds.
Prints each result live and logs to the database.

Usage:
    uv run python scripts/test_presence_local.py
"""

import logging
import time
from datetime import datetime, timezone

from db.database import get_connection, initialize_database
from db import queries
from core.notifications import send_notification
from core.presence import PresenceDetector
from core.tts import speak
from vital_types.db import PresenceConfig, PresenceLogEntry

CHECK_INTERVAL_SECONDS = 10
RUN_DURATION_SECONDS = 60
# After 1 minute of continuous presence, a break reminder fires.
MAX_CONTINUOUS_MINUTES = 1

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    """Run a short local presence detection session in the foreground."""
    initialize_database()

    queries.save_presence_config(
        PresenceConfig(
            enabled=True,
            max_continuous_minutes=MAX_CONTINUOUS_MINUTES,
            break_message="Test break — you've been at your desk for 1 minute. Stretch!",
            break_duration_minutes=5,
            check_interval_seconds=CHECK_INTERVAL_SECONDS,
        )
    )

    config = queries.get_presence_config()
    assert config is not None

    print("Vitál presence test")
    print(f"- Check interval: {CHECK_INTERVAL_SECONDS}s")
    print(f"- Run duration:   {RUN_DURATION_SECONDS}s")
    print(f"- Break after:    {config.max_continuous_minutes} minute(s) of continuous presence")
    print("- Sit in front of your webcam. Press Ctrl+C to stop early.\n")

    detector = PresenceDetector(config=config)
    deadline = time.time() + RUN_DURATION_SECONDS
    check_number = 0

    try:
        while time.time() < deadline:
            check_number += 1
            present = detector.check_once()
            if present:
                detector.continuous_seconds += detector.check_interval
            else:
                detector.continuous_seconds = 0

            status = "PRESENT" if present else "away"
            continuous_minutes = detector.continuous_seconds // 60
            print(
                f"[check {check_number}] {datetime.now().strftime('%H:%M:%S')}  "
                f"{status}  (continuous ~{continuous_minutes} min)"
            )

            queries.insert_presence_log(
                PresenceLogEntry(
                    detected=present,
                    checked_at=datetime.now(timezone.utc),
                    continuous_minutes=continuous_minutes,
                )
            )

            if present and detector.continuous_seconds >= detector.max_continuous:
                print("  -> Break threshold reached. Firing reminder...")
                message = detector.break_message
                send_notification("Vitál — Time for a Break", message, job_id="presence_break")
                speak(message)
                detector.continuous_seconds = 0

            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped early.")

    print("\nDone. Last 10 presence_log rows:")
    connection = get_connection()
    rows = connection.execute(
        "SELECT detected, checked_at, continuous_minutes FROM presence_log ORDER BY id DESC LIMIT 10;"
    ).fetchall()
    for row in reversed(rows):
        status = "present" if row["detected"] else "away"
        print(f"  {row['checked_at']}  {status}  (continuous ~{row['continuous_minutes']} min)")


if __name__ == "__main__":
    main()
