"""API module for History RAG remote access."""

from src.api.history_api import create_api_app, start_api_server

__all__ = ["create_api_app", "start_api_server"]
