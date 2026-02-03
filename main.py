"""Main entry point for MoEngage Q&A Slack Bot."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot.app import run_app
from src.utils.logger import logger


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
    logger.info("Starting MoEngage Q&A Agent...")

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
