"""Desktop push notifications via plyer."""

from datetime import datetime, timezone

from db import queries
from vital_types.core import NotificationResult
from vital_types.db import NotificationLogEntry

from core.app_config import get_notification_icon_path, is_demo_mode

MAX_NOTIFICATION_LENGTH = 500


def send_notification(
    title: str,
    message: str,
    job_id: str | None = None,
    log_delivery: bool = True,
) -> NotificationResult:
    """Send a desktop notification and optionally log it to the database."""
    if is_demo_mode():
        return NotificationResult(delivered=False, skipped_reason="demo_mode")

    trimmed_message = message.strip()
    if not trimmed_message:
        return NotificationResult(delivered=False, skipped_reason="empty_message")

    if len(trimmed_message) > MAX_NOTIFICATION_LENGTH:
        trimmed_message = trimmed_message[: MAX_NOTIFICATION_LENGTH - 3] + "..."

    try:
        from plyer import notification

        notify_kwargs: dict[str, str | int] = {
            "title": title,
            "message": trimmed_message,
            "app_name": "Vitál",
            "timeout": 10,
        }
        icon_path = get_notification_icon_path()
        if icon_path is not None:
            notify_kwargs["app_icon"] = icon_path

        notification.notify(**notify_kwargs)
    except Exception:
        return NotificationResult(delivered=False, skipped_reason="notification_failed")

    if log_delivery:
        queries.insert_notification_log(
            NotificationLogEntry(
                job_id=job_id or "manual",
                message=trimmed_message,
                delivered_at=datetime.now(timezone.utc),
                tts_spoken=False,
            )
        )

    return NotificationResult(delivered=True)
