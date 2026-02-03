"""History API - REST endpoints for remote history entry management."""

import json
from datetime import datetime
from typing import Optional

from aiohttp import web

from config.settings import settings
from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.utils.logger import logger


def verify_api_key(request: web.Request) -> bool:
    """Verify API key from request header.

    Args:
        request: aiohttp request object

    Returns:
        True if API key is valid, False otherwise
    """
    if not settings.history_api_key:
        logger.warning("API key not configured, rejecting request")
        return False

    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.history_api_key}"

    return auth_header == expected


async def health_check(request: web.Request) -> web.Response:
    """GET /api/health - Health check endpoint."""
    return web.json_response({
        "status": "ok",
        "service": "moengage-qna-agent",
        "api_enabled": settings.history_api_enabled
    })


async def get_history_stats(request: web.Request) -> web.Response:
    """GET /api/history/stats - Get history statistics."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        rag = get_history_rag()
        return web.json_response({
            "total_entries": rag.count(),
            "persist_dir": str(getattr(rag, 'persist_dir', 'N/A'))
        })
    except Exception as e:
        logger.error(f"API: Error getting stats: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def list_history_entries(request: web.Request) -> web.Response:
    """GET /api/history - List all history entries."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        rag = get_history_rag()
        entries = []

        for entry_id, entry in rag.entries.items():
            entries.append({
                "id": entry_id,
                "title": entry.title,
                "customer": entry.customer,
                "category": entry.category,
                "source": entry.source,
                "created_at": entry.created_at,
                "url": entry.url
            })

        return web.json_response({
            "count": len(entries),
            "entries": entries
        })
    except Exception as e:
        logger.error(f"API: Error listing entries: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def add_history_entry(request: web.Request) -> web.Response:
    """POST /api/history - Add a new history entry.

    Expected JSON body:
    {
        "title": str,
        "customer": str,
        "category": str,
        "query_summary": str,
        "solution": str,
        "url": str (optional),
        "channel_id": str (optional),
        "channel_name": str (optional),
        "referenced_docs": list (optional),
        "referenced_history": list (optional),
        "metadata": dict (optional),
        "source": str (optional, default "pdf_import")
    }
    """
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Validate required fields
    required = ["title", "customer", "category", "query_summary", "solution"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return web.json_response(
            {"error": f"Missing required fields: {missing}"},
            status=400
        )

    try:
        # Create HistoryEntry
        entry = HistoryEntry(
            id="",  # Will be auto-generated
            title=data["title"],
            customer=data["customer"],
            category=data["category"],
            query_summary=data["query_summary"],
            solution=data["solution"],
            created_at=data.get("created_at", datetime.now().isoformat()),
            url=data.get("url", ""),
            channel_id=data.get("channel_id", ""),
            channel_name=data.get("channel_name", ""),
            referenced_docs=data.get("referenced_docs", []),
            referenced_history=data.get("referenced_history", []),
            metadata=data.get("metadata", {}),
            source=data.get("source", "pdf_import")
        )

        # Add to RAG
        rag = get_history_rag()
        entry_id = rag.add_entry(entry)

        logger.info(f"API: Added history entry '{entry.title}' (ID: {entry_id})")

        return web.json_response({
            "success": True,
            "entry_id": entry_id,
            "title": entry.title
        })

    except Exception as e:
        logger.error(f"API: Error adding entry: {e}")
        return web.json_response({"error": str(e)}, status=500)


def create_api_app() -> web.Application:
    """Create aiohttp web application for API.

    Returns:
        Configured aiohttp Application
    """
    app = web.Application()

    # Add routes
    app.router.add_get("/api/health", health_check)
    app.router.add_get("/api/history", list_history_entries)
    app.router.add_get("/api/history/stats", get_history_stats)
    app.router.add_post("/api/history", add_history_entry)

    logger.info("History API routes registered")
    return app


async def start_api_server(port: Optional[int] = None) -> web.AppRunner:
    """Start the HTTP API server.

    Args:
        port: Port to listen on (default from settings)

    Returns:
        Running AppRunner instance
    """
    port = port or settings.history_api_port

    app = create_api_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"History API server started on port {port}")
    return runner
