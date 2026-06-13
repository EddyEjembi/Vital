"""Hugging Face Space entry point — exposes `demo` for the Gradio SDK harness.

Local desktop users should run app.py (Start_Vital.bat), which calls launch() itself.
This module mirrors app.py startup but does not call launch(); HF Spaces does that.
"""

import logging

from db import queries
from db.database import initialize_database
from ui.app_ui import build_gradio_app, start_background_services

from core.daily_greeting import deliver_daily_greeting_if_needed
from core.daily_startup import run_daily_startup
from core.weather import fetch_weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _prepare_space_app() -> None:
    """Run the same startup steps as app.py before building the Gradio UI."""
    initialize_database()
    onboarded = queries.check_onboarding_status()
    if not onboarded:
        logger.info("Space startup — no onboarded profile; showing onboarding UI.")
        return

    logger.info("Space startup — loading Vitál for onboarded user.")
    run_daily_startup()
    start_background_services()
    fetch_weather()
    deliver_daily_greeting_if_needed()


_prepare_space_app()

_onboarded = queries.check_onboarding_status()
demo, theme, css = build_gradio_app(onboarded=_onboarded)

from core.app_config import get_gradio_favicon_path

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=theme,
        css=css,
        favicon_path=get_gradio_favicon_path(),
    )