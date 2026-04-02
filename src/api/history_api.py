"""History API - REST endpoints for remote history entry management."""

import json
import secrets
from datetime import datetime
from typing import Optional

from aiohttp import web

from config.settings import settings

# Input validation constants
MAX_TITLE_LENGTH = 500
MAX_QUERY_LENGTH = 5000
MAX_SOLUTION_LENGTH = 10000
from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.knowledge.learning_store import get_learning_store
from src.knowledge.history_updater import LearningEntry
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

    # Use constant-time comparison to prevent timing attacks
    return secrets.compare_digest(auth_header, expected)


async def health_check(request: web.Request) -> web.Response:
    """GET /api/health - Comprehensive health check endpoint."""
    checks = {}

    # Check History RAG
    try:
        rag = get_history_rag()
        checks["history_rag"] = {
            "status": "ok",
            "entries": rag.count()
        }
    except Exception as e:
        checks["history_rag"] = {
            "status": "error",
            "error": str(e)
        }

    # Check Learning Store
    try:
        store = get_learning_store()
        checks["learning_store"] = {
            "status": "ok",
            "entries": store.count()
        }
    except Exception as e:
        checks["learning_store"] = {
            "status": "error",
            "error": str(e)
        }

    # Check configuration
    config_ok = all([
        settings.slack_bot_token,
        settings.slack_app_token,
        settings.anthropic_api_key,
        settings.csm_response_channel_id
    ])
    checks["configuration"] = {
        "status": "ok" if config_ok else "warning",
        "csm_channel_configured": bool(settings.csm_response_channel_id)
    }

    # Determine overall status
    all_ok = all(c.get("status") == "ok" for c in checks.values())
    any_error = any(c.get("status") == "error" for c in checks.values())

    if any_error:
        overall_status = "error"
    elif all_ok:
        overall_status = "ok"
    else:
        overall_status = "degraded"

    return web.json_response({
        "status": overall_status,
        "service": "moengage-qna-agent",
        "version": "2.0",
        "api_enabled": settings.history_api_enabled,
        "checks": checks
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
        logger.error(f"API: Error getting stats: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def list_history_entries(request: web.Request) -> web.Response:
    """GET /api/history - List all history entries with pagination."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        # Parse pagination parameters
        limit = int(request.query.get("limit", 100))
        offset = int(request.query.get("offset", 0))
        # Enforce limits
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        rag = get_history_rag()
        total = rag.count()

        all_entries = list(rag.entries.items())
        paginated = all_entries[offset:offset + limit]

        entries = []
        for entry_id, entry in paginated:
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
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
            "entries": entries
        })
    except Exception as e:
        logger.error(f"API: Error listing entries: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def export_history_entries(request: web.Request) -> web.Response:
    """GET /api/history/export - Export all history entries with full data."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        from dataclasses import asdict
        rag = get_history_rag()
        entries = []
        for entry_id, entry in rag.entries.items():
            entry_dict = asdict(entry) if hasattr(entry, '__dict__') else entry
            entries.append(entry_dict)

        return web.json_response({
            "count": len(entries),
            "entries": entries
        })
    except Exception as e:
        logger.error(f"API: Error exporting entries: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def export_learning_entries(request: web.Request) -> web.Response:
    """GET /api/learning/export - Export all learning entries with full data."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        store = get_learning_store()
        entries = []
        for entry_id, entry in store.entries.items():
            entries.append(entry.to_dict())

        return web.json_response({
            "count": len(entries),
            "entries": entries
        })
    except Exception as e:
        logger.error(f"API: Error exporting learning entries: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


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

    # Validate field lengths
    if len(data.get("title", "")) > MAX_TITLE_LENGTH:
        return web.json_response(
            {"error": f"Title exceeds maximum length of {MAX_TITLE_LENGTH}"},
            status=400
        )
    if len(data.get("query_summary", "")) > MAX_QUERY_LENGTH:
        return web.json_response(
            {"error": f"Query summary exceeds maximum length of {MAX_QUERY_LENGTH}"},
            status=400
        )
    if len(data.get("solution", "")) > MAX_SOLUTION_LENGTH:
        return web.json_response(
            {"error": f"Solution exceeds maximum length of {MAX_SOLUTION_LENGTH}"},
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
        logger.error(f"API: Error adding entry: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def get_learning_stats(request: web.Request) -> web.Response:
    """GET /api/learning/stats - Get learning statistics."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        store = get_learning_store()
        stats = store.get_statistics()
        return web.json_response(stats)
    except Exception as e:
        logger.error(f"API: Error getting learning stats: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def list_learning_entries(request: web.Request) -> web.Response:
    """GET /api/learning - List all learning entries."""
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        limit = int(request.query.get("limit", 100))
        offset = int(request.query.get("offset", 0))
        # Enforce limits
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        store = get_learning_store()
        entries = []
        total = store.count()

        all_entries = list(store.entries.items())
        paginated = all_entries[offset:offset + limit]

        for entry_id, entry in paginated:
            entries.append({
                "id": entry_id,
                "original_query": entry.original_query[:200],
                "customer": entry.customer,
                "category": entry.category,
                "iteration_count": entry.iteration_count,
                "created_at": entry.created_at,
                "completed_at": entry.completed_at,
                "csm_user_name": entry.csm_user_name,
                "has_query_lesson": bool(entry.learning_points.query_lesson),
                "has_search_lesson": bool(entry.learning_points.search_lesson),
                "has_response_lesson": bool(entry.learning_points.response_lesson),
            })

        return web.json_response({
            "count": len(entries),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
            "entries": entries
        })
    except Exception as e:
        logger.error(f"API: Error listing learning entries: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def add_learning_entry(request: web.Request) -> web.Response:
    """POST /api/learning - Add a new learning entry.

    Expected JSON body: LearningEntry.to_dict() format
    """
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Validate required fields
    required = ["original_query"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return web.json_response(
            {"error": f"Missing required fields: {missing}"},
            status=400
        )

    try:
        # Create LearningEntry from dict
        entry = LearningEntry.from_dict(data)

        # Add to store
        store = get_learning_store()
        entry_id = store.add_entry(entry)

        logger.info(f"API: Added learning entry (ID: {entry_id})")

        return web.json_response({
            "success": True,
            "entry_id": entry_id,
            "original_query": entry.original_query[:100]
        })

    except Exception as e:
        logger.error(f"API: Error adding learning entry: {e}", exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def run_retrospective(request: web.Request) -> web.Response:
    """Run daily retrospective: analyze recent learnings and upgrade skills.

    Triggered by Cloud Scheduler or manual POST.
    """
    if not verify_api_key(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        learning_store = get_learning_store()
        recent_entries = learning_store.get_recent_entries(hours=24)

        if not recent_entries:
            logger.info("[RETROSPECTIVE] No recent learning entries, skipping")
            return web.json_response({"status": "skip", "reason": "no recent learnings"})

        # Format learning entries for Claude
        entries_text_parts = []
        entry_ids = []
        for entry in recent_entries:
            lp = entry.learning_points
            parts = []
            if hasattr(lp, 'query_lesson') and lp.query_lesson:
                parts.append(f"  문의 해석: {lp.query_lesson}")
            if hasattr(lp, 'search_lesson') and lp.search_lesson:
                parts.append(f"  검색 전략: {lp.search_lesson}")
            if hasattr(lp, 'response_lesson') and lp.response_lesson:
                parts.append(f"  답변 작성: {lp.response_lesson}")
            if parts:
                entries_text_parts.append(
                    f"- 원본 문의: {entry.original_query[:100]}\n" + "\n".join(parts)
                )
                entry_ids.append(entry.id)

        if not entries_text_parts:
            return web.json_response({"status": "skip", "reason": "no actionable learnings"})

        entries_text = "\n\n".join(entries_text_parts)

        # Load current skill contents for dedup
        from src.knowledge.skill_store import get_skill_store
        skill_store = get_skill_store()
        current_skills = {
            "write_response": skill_store.get_skill("write_response")[:500],
            "query_optimizer": skill_store.get_skill("query_optimizer")[:500],
            "rerank": skill_store.get_skill("rerank")[:500],
        }

        # Run retrospective with Claude
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60.0)
        retro_prompt = skill_store.get_skill("retrospective")

        prompt = f"""## 최근 24시간 학습 데이터 ({len(recent_entries)}건)
{entries_text}

## 현재 스킬 요약 (중복 방지용)
write_response: {current_skills['write_response']}...
query_optimizer: {current_skills['query_optimizer']}...
rerank: {current_skills['rerank']}...

위 학습 데이터를 분석하여 스킬 업그레이드 사항을 JSON으로 출력하세요."""

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=retro_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text if response.content else "{}"

        # Parse response
        from src.llm.claude_client import extract_json_safely
        result = extract_json_safely(response_text)

        if not isinstance(result, dict) or "upgrades" not in result:
            logger.warning(f"[RETROSPECTIVE] Invalid response format")
            return web.json_response({"status": "error", "reason": "invalid response"})

        # Apply upgrades to skill files
        upgrades = result.get("upgrades", {})
        applied = []
        today = datetime.now().strftime("%Y-%m-%d")
        for skill_key, upgrade_text in upgrades.items():
            if upgrade_text and upgrade_text.strip():
                section = f"## 회고 기반 추가 지침 ({today})\n{upgrade_text.strip()}"
                skill_store.append_to_skill(skill_key, section)
                applied.append(skill_key)
                logger.info(f"[RETROSPECTIVE] Upgraded skill: {skill_key}")

        summary = result.get("summary", "")
        logger.info(f"[RETROSPECTIVE] Complete. Upgraded {len(applied)} skills: {applied}. Summary: {summary}")

        return web.json_response({
            "status": "ok",
            "entries_analyzed": len(recent_entries),
            "skills_upgraded": applied,
            "summary": summary,
        })

    except Exception as e:
        logger.error(f"[RETROSPECTIVE] Failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def create_api_app() -> web.Application:
    """Create aiohttp web application for API.

    Returns:
        Configured aiohttp Application
    """
    app = web.Application()

    # History routes
    app.router.add_get("/api/health", health_check)
    app.router.add_get("/api/history", list_history_entries)
    app.router.add_get("/api/history/stats", get_history_stats)
    app.router.add_get("/api/history/export", export_history_entries)
    app.router.add_post("/api/history", add_history_entry)

    # Learning routes
    app.router.add_get("/api/learning", list_learning_entries)
    app.router.add_get("/api/learning/stats", get_learning_stats)
    app.router.add_get("/api/learning/export", export_learning_entries)
    app.router.add_post("/api/learning", add_learning_entry)

    # Retrospective route
    app.router.add_post("/api/retrospective", run_retrospective)

    logger.info("History & Learning API routes registered")
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
