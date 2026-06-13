"""Main Gradio Blocks app — assembles all tabs."""

import base64
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import gradio as gr

from core.app_config import get_gradio_favicon_path, get_logo_path
from core.daily_greeting import deliver_daily_greeting_if_needed
from core.daily_startup import run_daily_startup
from core.presence import start_presence
from core.scheduler import reload_scheduler_jobs, start_scheduler
from db import queries
from ui.coach import build_coach_tab
from ui.dashboard import (
    build_dashboard_tab,
    empty_medication_checklist_update,
    get_dashboard_updates,
)
from ui.movement import build_movement_tab
from ui.nutrition import build_nutrition_tab
from ui.onboarding import build_onboarding_ui
from ui.report import build_report_tab
from ui.settings import build_settings_refresh_updates, build_settings_tab
from ui.theme import VITAL_CSS, get_vital_theme

logger = logging.getLogger(__name__)


def start_background_services() -> None:
    """Start scheduler and presence after onboarding or daily launch."""
    start_scheduler()
    start_presence()


_startup_tasks_lock = threading.Lock()


def _run_startup_tasks_in_background() -> None:
    """Run daily startup (LLM plan, greeting TTS) without blocking page load.

    These can take minutes on a Modal cold start; running them inside the
    page-load handler kept loading spinners on every output component.
    The lock ensures rapid browser refreshes don't run them concurrently.
    """
    def runner() -> None:
        if not _startup_tasks_lock.acquire(blocking=False):
            logger.info("Startup tasks already running — skipping duplicate.")
            return
        try:
            run_daily_startup()
            deliver_daily_greeting_if_needed()
            reload_scheduler_jobs()
        except Exception:
            logger.exception("Background startup tasks failed.")
        finally:
            _startup_tasks_lock.release()

    threading.Thread(target=runner, name="vital-startup-tasks", daemon=True).start()


_LOGO_MIME_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _build_header_html() -> str:
    """Render the app header, embedding the logo as a data URI when present."""
    logo_path = get_logo_path()
    logo_html = ""
    if logo_path:
        path = Path(logo_path)
        mime = _LOGO_MIME_TYPES.get(path.suffix.lower())
        if mime:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            logo_html = (
                f'<img src="data:{mime};base64,{encoded}" alt="Vitál logo" '
                'class="vital-logo" />'
            )
    return (
        '<div class="vital-header">'
        f"{logo_html}"
        '<div class="vital-header-copy">'
        '<div class="vital-wordmark">Vitál</div>'
        '<div class="vital-tagline">Personal wellness that lives with your workday.</div>'
        "</div>"
        "</div>"
    )


def build_gradio_app(
    onboarded: bool,
    on_onboarded: Callable[[], None] | None = None,
) -> tuple[gr.Blocks, gr.Theme, str]:
    """Assemble the Gradio app for first-run onboarding or daily use."""
    favicon = get_gradio_favicon_path()
    theme = get_vital_theme()

    with gr.Blocks(title="Vitál") as app:
        gr.HTML(_build_header_html())

        dashboard_col = gr.Column(visible=onboarded)
        with dashboard_col:
            with gr.Tabs():
                with gr.Tab("Home"):
                    dashboard_parts = build_dashboard_tab(initial_onboarded=onboarded)
                with gr.Tab("Nutrition"):
                    build_nutrition_tab()
                with gr.Tab("Movement"):
                    build_movement_tab()
                with gr.Tab("Coach"):
                    build_coach_tab()
                with gr.Tab("Report"):
                    build_report_tab()
                with gr.Tab("Settings"):
                    settings_parts = build_settings_tab()

        onboarding_col = gr.Column(visible=not onboarded)
        with onboarding_col:
            def handle_onboarding_complete() -> None:
                """Run post-onboarding startup tasks."""
                run_daily_startup()
                start_background_services()
                reload_scheduler_jobs()
                deliver_daily_greeting_if_needed()
                if on_onboarded is not None:
                    on_onboarded()

            build_onboarding_ui(
                handle_onboarding_complete,
                dashboard_col,
                onboarding_col,
                dashboard_parts,
            )

        settings_refresh_keys = [
            "name",
            "wake_time",
            "sleep_time",
            "hydration_liters",
            "medications_text",
            "presence_minutes",
            "desk_break_preview",
            "tts_hydration",
            "tts_exercise",
            "tts_medication",
            "tts_meal",
        ]
        settings_outputs = [settings_parts[key] for key in settings_refresh_keys]

        def refresh_settings_from_db() -> tuple:
            """Fast DB read for settings — must not block on LLM or TTS."""
            logger.info("Refreshing settings fields from DB.")
            if not queries.check_onboarding_status():
                return tuple(gr.update() for _ in settings_refresh_keys)
            return build_settings_refresh_updates()

        def on_page_load() -> tuple:
            """Sync dashboard with database state on every browser load or refresh."""
            is_onboarded = queries.check_onboarding_status()
            logger.info("Page load — onboarded=%s", is_onboarded)

            if is_onboarded:
                # Heavy work (LLM plan, greeting TTS) runs in a thread; the page
                # renders immediately from whatever is already in the DB.
                _run_startup_tasks_in_background()
                greeting_value, briefing_value, metrics_value, checklist_payload = (
                    get_dashboard_updates()
                )
                return (
                    gr.update(visible=True),
                    gr.update(visible=False),
                    gr.update(value=greeting_value),
                    gr.update(value=briefing_value),
                    gr.update(value=metrics_value),
                    gr.update(**checklist_payload),
                )

            return (
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(value=""),
                gr.update(**empty_medication_checklist_update()),
            )

        # Settings refresh runs in its own load handler with the spinner overlay
        # disabled, so the fields never show a blocking loader.
        app.load(
            refresh_settings_from_db,
            outputs=settings_outputs,
            show_progress="hidden",
        )

        app.load(
            on_page_load,
            outputs=[
                dashboard_col,
                onboarding_col,
                dashboard_parts["greeting"],
                dashboard_parts["briefing"],
                dashboard_parts["metrics"],
                dashboard_parts["checklist"],
            ],
            show_progress="minimal",
        )

    if favicon:
        # Kept for callers that read it; launch(favicon_path=...) is authoritative.
        app.favicon_path = favicon

    return app, theme, VITAL_CSS
