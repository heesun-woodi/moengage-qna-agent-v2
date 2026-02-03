"""Slack Bot application using Bolt framework."""

import asyncio
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from config.settings import settings
from src.utils.logger import logger


def create_app() -> AsyncApp:
    """Create and configure the Slack Bolt app.

    Returns:
        Configured AsyncApp instance
    """
    app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret
    )

    # Register event handlers
    from src.bot.handlers import register_handlers
    register_handlers(app)

    logger.info("Slack app created and handlers registered")
    return app


async def start_socket_mode(app: AsyncApp):
    """Start the app in Socket Mode.

    Args:
        app: The Slack app instance
    """
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    logger.info("Starting Slack app in Socket Mode...")
    await handler.start_async()


async def start_http_api():
    """Start the HTTP API server for remote history management."""
    from src.api.history_api import start_api_server

    port = settings.history_api_port
    await start_api_server(port)
    logger.info(f"HTTP API server running on port {port}")

    # Keep running
    while True:
        await asyncio.sleep(3600)


async def run_all():
    """Run both Slack Socket Mode and HTTP API concurrently."""
    app = create_app()

    tasks = [start_socket_mode(app)]

    # Start HTTP API if enabled
    if settings.history_api_enabled:
        tasks.append(start_http_api())
        logger.info("History API enabled - starting HTTP server")
    else:
        logger.info("History API disabled (set HISTORY_API_ENABLED=true to enable)")

    await asyncio.gather(*tasks)


def run_app():
    """Run the Slack app (blocking)."""
    asyncio.run(run_all())


# Global app instance
_app: Optional[AsyncApp] = None


def get_app() -> AsyncApp:
    """Get or create the global app instance."""
    global _app
    if _app is None:
        _app = create_app()
    return _app


if __name__ == "__main__":
    run_app()
