"""Vitál entry point — starts all components."""

import logging

from db import queries
from db.database import initialize_database
from ui.app_ui import build_gradio_app, start_background_services

from core.app_config import (
    get_gradio_favicon_path,
    get_gradio_launch_in_browser,
    get_gradio_server_host,
    get_gradio_server_port,
)
from core.daily_greeting import deliver_daily_greeting_if_needed
from core.daily_startup import run_daily_startup
from core.weather import fetch_weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _launch_gradio_app(app, theme, css) -> None:
    """Launch the Gradio app using host/port/browser settings from .env."""
    app.launch(
        server_name=get_gradio_server_host(),
        server_port=get_gradio_server_port(),
        inbrowser=get_gradio_launch_in_browser(),
        theme=theme,
        css=css,
        favicon_path=get_gradio_favicon_path(),
    )


def start() -> None:
    """Initialise the database and launch the application."""
    initialize_database()
    onboarded = queries.check_onboarding_status()

    if not onboarded:
        logger.info("First run — launching onboarding.")
        app, theme, css = build_gradio_app(onboarded=False)
        _launch_gradio_app(app, theme, css)
        return

    logger.info("Starting Vitál...")
    run_daily_startup()
    start_background_services()
    fetch_weather()

    deliver_daily_greeting_if_needed()

    app, theme, css = build_gradio_app(onboarded=True)
    _launch_gradio_app(app, theme, css)


if __name__ == "__main__":
    start()
