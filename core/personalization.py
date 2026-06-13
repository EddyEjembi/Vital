"""Personalize coach messages with the user's name."""


def first_name(full_name: str) -> str:
    """Return the first token of a display name."""
    return full_name.strip().split()[0] if full_name.strip() else ""


def personalize_message(full_name: str, message: str) -> str:
    """Prefix a message with the user's first name when it is not already present."""
    trimmed = message.strip()
    if not trimmed:
        return trimmed

    name_token = first_name(full_name)
    if not name_token:
        return trimmed

    if name_token.lower() in trimmed.lower():
        return trimmed

    return f"{name_token}, {trimmed}"


def build_desk_break_message(full_name: str, break_minutes: int) -> str:
    """Build the fixed desk-break TTS message with name and duration."""
    name_token = first_name(full_name) or "there"
    minutes = max(5, break_minutes)
    return (
        f"{name_token}, you've been at your desk for {minutes} minutes. "
        "It's time to take a break and stretch."
    )
