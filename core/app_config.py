"""Shared runtime configuration for core services."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def is_demo_mode() -> bool:
    """Return true when the app is running in HuggingFace demo mode."""
    return os.getenv("DEMO_MODE", "false").lower() == "true"


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_gradio_server_host() -> str:
    """Return the Gradio server bind address (127.0.0.1 local, 0.0.0.0 on HF Space)."""
    return os.getenv("VITAL_SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"


def get_gradio_server_port() -> int:
    """Return the Gradio server port."""
    try:
        return int(os.getenv("VITAL_SERVER_PORT", "7860"))
    except ValueError:
        return 7860


def get_gradio_launch_in_browser() -> bool:
    """Return whether to open the browser automatically on launch."""
    return _env_bool("VITAL_LAUNCH_IN_BROWSER", True)


def _find_asset(stems: list[str], extensions: list[str]) -> Path | None:
    """Return the first existing asset matching any stem/extension pair."""
    for stem in stems:
        for extension in extensions:
            candidate = ASSETS_DIR / f"{stem}.{extension}"
            if candidate.exists():
                return candidate
    return None


def get_notification_icon_path() -> str | None:
    """Return the notification icon path when a supported asset exists."""
    ico_asset = _find_asset(["vital_icon", "favicon", "icon"], ["ico"])
    if ico_asset is not None:
        return str(ico_asset)

    # Windows plyer balloon tips require .ico; PNG crashes in a background thread.
    if sys.platform != "win32":
        png_asset = _find_asset(["vital_icon", "favicon", "icon", "logo"], ["png"])
        if png_asset is not None:
            return str(png_asset)

    return None


def get_gradio_favicon_path() -> str | None:
    """Return the browser favicon path when a supported asset exists."""
    asset = _find_asset(
        ["favicon", "vital_icon", "icon", "logo"],
        ["ico", "png", "svg"],
    )
    if asset is not None:
        return str(asset)
    return None


def get_logo_path() -> str | None:
    """Return the app logo path for the UI header when present."""
    asset = _find_asset(
        ["logo", "vital_logo", "vital"],
        ["png", "svg", "webp", "jpg", "jpeg"],
    )
    if asset is not None:
        return str(asset)
    return None
