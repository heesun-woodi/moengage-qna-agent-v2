"""Logging configuration for MoEngage Q&A Agent."""

import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "moengage_agent",
    level: Optional[str] = None
) -> logging.Logger:
    """Set up and return a configured logger.

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Configured logger instance
    """
    from config.settings import settings

    log_level = level or settings.log_level

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level.upper()))

    # Format
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


# Default logger instance
logger = setup_logger()
