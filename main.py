"""Main entry point for MoEngage Q&A Slack Bot."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot.app import run_app
from src.utils.logger import logger
from config.settings import settings


def validate_configuration():
    """Validate required configuration at startup.

    Raises:
        ValueError: If required configuration is missing
    """
    errors = []

    # Required Slack tokens
    if not settings.slack_bot_token:
        errors.append("SLACK_BOT_TOKEN is required")
    elif not settings.slack_bot_token.startswith("xoxb-"):
        errors.append("SLACK_BOT_TOKEN must start with 'xoxb-'")

    if not settings.slack_app_token:
        errors.append("SLACK_APP_TOKEN is required for Socket Mode")
    elif not settings.slack_app_token.startswith("xapp-"):
        errors.append("SLACK_APP_TOKEN must start with 'xapp-'")

    # Required API key
    if not settings.anthropic_api_key:
        errors.append("ANTHROPIC_API_KEY is required")

    # v2 Required: CSM response channel
    if not settings.csm_response_channel_id:
        errors.append(
            "CSM_RESPONSE_CHANNEL_ID is required for v2. "
            "This is the channel where bot posts responses for CSM review."
        )
    elif not settings.csm_response_channel_id.startswith("C"):
        errors.append(
            "CSM_RESPONSE_CHANNEL_ID must be a valid Slack channel ID (starts with 'C')"
        )

    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")


def log_history_entries():
    """Log all stored history entries on startup."""
    try:
        from src.knowledge.history_rag import get_history_rag
        rag = get_history_rag()
        entries = getattr(rag, 'entries', {}) or getattr(rag, '_entries', {})

        logger.info(f"=== Stored History Entries: {len(entries)} ===")
        for idx, (entry_id, entry) in enumerate(entries.items(), 1):
            title = entry.title[:80] + "..." if len(entry.title) > 80 else entry.title
            query = entry.query_summary[:150] + "..." if len(entry.query_summary) > 150 else entry.query_summary
            solution = entry.solution[:300] + "..." if len(entry.solution) > 300 else entry.solution

            # Get new fields with defaults for backward compatibility
            channel_name = getattr(entry, 'channel_name', '') or ''
            thread_url = getattr(entry, 'url', '') or ''
            ref_docs = getattr(entry, 'referenced_docs', []) or []
            ref_history = getattr(entry, 'referenced_history', []) or []

            logger.info(f"")
            logger.info(f"  [{idx}] ID: {entry_id}")
            logger.info(f"      Title: {title}")
            logger.info(f"      Customer: {entry.customer}")
            logger.info(f"      Channel: {channel_name}")
            logger.info(f"      Category: {entry.category}")
            logger.info(f"      Thread URL: {thread_url}")
            logger.info(f"      Created: {entry.created_at}")
            logger.info(f"      Query: {query}")
            logger.info(f"      Solution: {solution}")
            if ref_docs:
                logger.info(f"      Referenced Docs: {len(ref_docs)} docs")
            if ref_history:
                logger.info(f"      Referenced History: {len(ref_history)} entries")
        logger.info("=" * 50)
    except Exception as e:
        logger.warning(f"Could not log history entries: {e}")


def main():
    """Main entry point."""
    logger.info("Starting MoEngage Q&A Agent v2...")

    # Validate configuration before starting
    try:
        validate_configuration()
        logger.info("Configuration validation passed")
    except ValueError as e:
        logger.error(f"Startup aborted: {e}")
        sys.exit(1)

    # Log stored history entries
    log_history_entries()

    try:
        run_app()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
