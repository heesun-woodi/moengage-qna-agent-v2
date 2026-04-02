"""Slack event handlers for emoji reactions and app mentions."""

import json
import re
import asyncio
import time
from typing import Dict, Any, Optional
from datetime import datetime

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from config.settings import settings
from src.bot.state_machine import (
    MessageState,
    get_message_state,
    set_message_state,
    can_process_ticket,
    can_process_complete
)
from src.bot.formatters import (
    format_support_response,
    format_error_response,
    format_csm_ticket_response,
    format_improved_response,
    format_learning_saved_confirmation,
    build_csm_ticket_blocks,
    build_csm_resource_list_blocks,
    build_improved_response_blocks,
    build_delivered_confirmation_blocks,
    format_customer_response,
    update_button_value,
)
from src.knowledge.hybrid_searcher import search_and_format, hybrid_search, multi_query_hybrid_search, format_context_for_llm, rerank_results
from src.knowledge.history_updater import (
    add_from_slack_thread,
    LearningEntry,
    LearningPoints,
    QueryInterpretation,
    SearchHistory,
    ResponseEvolution,
    SearchIteration
)
from src.knowledge.feedback_store import get_feedback_store
from src.knowledge.learning_store import get_learning_store, save_learning_entry, get_learning_for_query
from src.llm.claude_client import (
    generate_csm_response,
    extract_learning_points,
    extract_learning_from_thread,
    generate_improved_response,
    get_claude_client
)
from src.llm.thread_analyzer import analyze_slack_thread, detect_resolution_keywords
from src.llm.query_optimizer import analyze_query
from src.utils.logger import logger
from src.bot.history_command import handle_history_command

# In-memory store for active CSM ticket sessions
# Maps: csm_thread_ts -> session_data
_csm_sessions: Dict[str, Dict[str, Any]] = {}
_session_lock = asyncio.Lock()

# Session TTL in seconds (24 hours)
SESSION_TTL_SECONDS = 86400


def _parse_bot_message(message: dict) -> tuple:
    """Extract original_query, original_channel, original_ts from bot's resource list message.

    Returns:
        (original_query, original_channel, original_ts) tuple
    """
    import re
    text = message.get("text", "")
    blocks = message.get("blocks", [])

    original_query = ""
    original_channel = ""
    original_ts = ""

    # Try blocks first, then fallback text
    search_text = ""
    for block in blocks:
        block_text = block.get("text", {}).get("text", "") if isinstance(block.get("text"), dict) else ""
        search_text += block_text + "\n"
    if not search_text.strip():
        search_text = text

    # Extract channel + ts from message link
    link_match = re.search(r'https://[^/]+/archives/([A-Z0-9]+)/p(\d+)', search_text)
    if link_match:
        original_channel = link_match.group(1)
        raw_ts = link_match.group(2)
        if len(raw_ts) > 10:
            original_ts = raw_ts[:10] + "." + raw_ts[10:]
        else:
            original_ts = raw_ts

    # Extract query from blockquote (after "고객 문의 내용" or "고객 문의", with optional mrkdwn bold *)
    query_match = re.search(r'\*?(?:고객 문의 내용|고객 문의)\*?[:\s]*\n?>(.*?)(?:\n[^>]|\Z)', search_text, re.DOTALL)
    if query_match:
        original_query = query_match.group(1).strip()
    elif '고객 문의:' in search_text:
        fallback_match = re.search(r'고객 문의:\s*(.+)', search_text)
        if fallback_match:
            original_query = fallback_match.group(1).strip()[:500]

    return original_query, original_channel, original_ts


async def _auto_extract_learning(session: dict, thread_ts: str):
    """Background task: extract and save learning from current session state."""
    try:
        improved = session.get("improved_responses", [])
        if not improved:
            return

        learning_points = await extract_learning_points(
            original_query=session.get("original_query", ""),
            initial_response=session.get("initial_response", ""),
            csm_feedback=session.get("feedback", []),
            improved_responses=improved,
            final_response=improved[-1]
        )

        has_learning = any(
            learning_points.get(k)
            for k in ["query_lesson", "search_lesson", "response_lesson"]
        )
        if not has_learning:
            logger.info("[AUTO-LEARN] No learning points extracted")
            return

        from src.knowledge.history_updater import LearningEntry, LearningPoints
        entry = LearningEntry(
            original_query=session.get("original_query", ""),
            original_channel=session.get("original_channel", ""),
            original_ts=session.get("original_ts", ""),
            learning_points=LearningPoints(
                query_lesson=learning_points.get("query_lesson", ""),
                search_lesson=learning_points.get("search_lesson", ""),
                response_lesson=learning_points.get("response_lesson", ""),
            ),
            category=learning_points.get("category", "기타"),
            iteration_count=session.get("iteration_count", 0),
            created_at=session.get("created_at", datetime.now().isoformat()),
            completed_at=datetime.now().isoformat(),
        )
        entry_id = await save_learning_entry(entry)
        session["auto_learned_at"] = time.time()
        logger.info(f"[AUTO-LEARN] Saved entry {entry_id} from session {thread_ts}")
    except Exception as e:
        logger.warning(f"[AUTO-LEARN] Failed: {e}")


async def _reconstruct_session(client, channel: str, thread_ts: str) -> Optional[dict]:
    """Reconstruct session from Slack thread history.

    Fetches the bot's root message in the thread, parses the original query
    and customer message info, re-runs search, and creates a new session.
    """
    try:
        thread_result = await client.conversations_replies(
            channel=channel, ts=thread_ts, limit=1
        )
        messages = thread_result.get("messages", [])
        if not messages:
            logger.warning(f"[SESSION RESTORE] No messages found in thread {thread_ts}")
            return None

        root_msg = messages[0]
        original_query, original_channel, original_ts = _parse_bot_message(root_msg)
        if not original_query:
            logger.warning(f"[SESSION RESTORE] Could not parse original query from thread {thread_ts}")
            return None

        logger.info(f"[SESSION RESTORE] Parsed query: {original_query[:80]}")

        # Analyze query for sub-questions and optimized search
        query_analysis = None
        try:
            query_analysis = await analyze_query(original_query)
        except Exception as e:
            logger.warning(f"[SESSION RESTORE] Query analysis failed: {e}")

        # Re-run search (multi-query if sub-questions detected)
        sub_questions = query_analysis.get("sub_questions", []) if query_analysis else []
        if len(sub_questions) > 1:
            search_results = await multi_query_hybrid_search(query_analysis)
        else:
            search_results = await hybrid_search(original_query, query_analysis=query_analysis)

        all_results = list(search_results)
        try:
            search_results = await rerank_results(original_query, search_results)
        except Exception as e:
            logger.warning(f"[SESSION RESTORE] Rerank failed: {e}")

        context = format_context_for_llm(search_results, current_channel_id=None)

        # Retrieve learning from past similar cases
        learning_data = None
        learning_context = ""
        try:
            learning_data = await get_learning_for_query(original_query, top_k=3)
            if learning_data and learning_data.get("has_learning"):
                learning_context = _format_learning_context(learning_data)
                context = f"{context}\n\n{learning_context}"
                logger.info(f"[SESSION RESTORE] Injected learning from similar cases")
        except Exception as e:
            logger.warning(f"[SESSION RESTORE] Learning retrieval failed: {e}")

        session = {
            "original_channel": original_channel,
            "original_ts": original_ts,
            "original_query": original_query,
            "context": context,
            "search_results": search_results,
            "all_search_results": all_results,
            "initial_response": "",
            "iteration_count": 0,
            "feedback": [],
            "improved_responses": [],
            "search_queries": [original_query],
            "search_results_titles": [r.title for r in search_results],
            "referenced_docs": [r.url for r in search_results if r.source == "moengage_docs"],
            "referenced_history": [],
            "created_at": datetime.now().isoformat(),
            "created_at_ts": time.time(),
            "query_analysis": query_analysis,
            "learning_data": learning_data,
            "learning_context": learning_context,
            "response_messages": {},
            "delivered_responses": set(),
        }
        async with _session_lock:
            _csm_sessions[thread_ts] = session
        logger.info(f"[SESSION RESTORE] Reconstructed for thread {thread_ts}")
        return session
    except Exception as e:
        logger.error(f"[SESSION RESTORE] Failed: {e}", exc_info=True)
        return None


async def cleanup_stale_sessions():
    """Remove sessions older than TTL."""
    async with _session_lock:
        cutoff = time.time() - SESSION_TTL_SECONDS
        stale = [
            k for k, v in _csm_sessions.items()
            if v.get("created_at_ts", 0) < cutoff
        ]
        for k in stale:
            del _csm_sessions[k]
            logger.info(f"Cleaned up stale session: {k}")
        if stale:
            logger.info(f"Cleaned up {len(stale)} stale sessions")

# PDF import related imports (lazy loaded)
PDF_IMPORTER_AVAILABLE = False
try:
    from src.knowledge.pdf_importer import PDFHistoryImporter, download_slack_file
    PDF_IMPORTER_AVAILABLE = True
    logger.info("PDF importer loaded successfully")
except ImportError as e:
    logger.warning(f"PDF importer not available (ImportError): {e}")
except Exception as e:
    logger.warning(f"PDF importer not available (Error): {e}")


def register_handlers(app: AsyncApp):
    """Register all event handlers with the app.

    Args:
        app: The Slack Bolt app instance
    """
    logger.info(f"Registering handlers... PDF_IMPORTER_AVAILABLE={PDF_IMPORTER_AVAILABLE}")

    # Catch-all middleware for debugging
    @app.middleware
    async def log_all_events(logger_middleware, body, next):
        """Log all incoming events for debugging."""
        event_type = body.get("event", {}).get("type", "unknown")
        logger.info(f"[MIDDLEWARE] Received event type: {event_type}")
        await next()

    @app.event("reaction_added")
    async def handle_reaction_added(event: Dict[str, Any], client: AsyncWebClient):
        """Handle reaction_added events for ticket and complete emojis."""
        reaction = event.get("reaction", "")
        item = event.get("item", {})
        user = event.get("user", "")

        # Only handle message reactions
        if item.get("type") != "message":
            return

        channel = item.get("channel", "")
        message_ts = item.get("ts", "")

        logger.info(f"Reaction added: {reaction} on {channel}/{message_ts} by {user}")

        # Handle ticket emoji (support both custom :ticket: and standard :admission_tickets:)
        if reaction in (settings.ticket_emoji, "admission_tickets"):
            await handle_ticket_emoji(client, channel, message_ts, user)

        # Handle complete emoji
        elif reaction == settings.complete_emoji:
            await handle_complete_emoji(client, channel, message_ts, user)

        # Handle feedback emojis
        elif reaction == settings.positive_feedback_emoji:
            await handle_feedback_emoji(client, channel, message_ts, user, "positive")

        elif reaction == settings.negative_feedback_emoji:
            await handle_feedback_emoji(client, channel, message_ts, user, "negative")

    @app.event("reaction_removed")
    async def handle_reaction_removed(event: Dict[str, Any], client: AsyncWebClient):
        """Handle reaction_removed events."""
        reaction = event.get("reaction", "")
        item = event.get("item", {})

        if item.get("type") != "message":
            return

        channel = item.get("channel", "")
        message_ts = item.get("ts", "")

        logger.info(f"Reaction removed: {reaction} on {channel}/{message_ts}")

        # Reset state if ticket emoji removed and not yet answered
        if reaction == settings.ticket_emoji:
            state = await get_message_state(channel, message_ts)
            if state == MessageState.PROCESSING:
                await set_message_state(channel, message_ts, MessageState.IDLE)
                logger.info(f"Reset state to IDLE for {channel}/{message_ts}")

    @app.event("message")
    async def handle_message(event: Dict[str, Any], client: AsyncWebClient):
        """Handle message events for CSM thread replies and logging."""
        subtype = event.get("subtype")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts")
        message_ts = event.get("ts", "")
        text = event.get("text", "")
        user = event.get("user", "")
        bot_id = event.get("bot_id")

        # Log all message events for debugging
        logger.info(f"[MESSAGE EVENT] subtype={subtype}, channel={channel}, text={str(text)[:50]}..., files={len(event.get('files', []))}")

        # Skip bot messages
        if bot_id:
            return

        # Skip non-thread messages
        if not thread_ts:
            return

        # Skip message subtypes like message_changed, etc.
        if subtype:
            return

        # Skip messages that contain bot mentions — app_mention event handles those
        import re as _re
        if text and _re.search(r'<@[A-Z0-9]+>', text):
            return

        # Check if this is a reply in the CSM response channel
        if channel == settings.csm_response_channel_id:
            # Try to find or reconstruct session
            if thread_ts not in _csm_sessions:
                session = await _reconstruct_session(client, channel, thread_ts)
                if not session:
                    return  # Not a bot thread or parse failed → ignore

            logger.info(f"[CSM THREAD REPLY] Processing reply in thread {thread_ts}")
            await handle_csm_thread_reply(
                client=client,
                channel=channel,
                thread_ts=thread_ts,
                message_ts=message_ts,
                text=text,
                user=user
            )

    @app.event("app_mention")
    async def handle_app_mention(event: Dict[str, Any], client: AsyncWebClient):
        """Handle app mention events - PDF import or CSM conversational Q&A."""
        logger.info(f"[APP_MENTION EVENT] Received! Full event: {event}")

        channel = event.get("channel", "")
        message_ts = event.get("ts", "")
        text = event.get("text", "") or ""
        files = event.get("files", [])
        user = event.get("user", "")

        logger.info(f"[APP_MENTION] channel={channel}, ts={message_ts}, user={user}, text={text[:100] if text else 'None'}..., files_in_event={len(files)}")

        # If files not in event, fetch the message to get files
        if not files:
            try:
                result = await client.conversations_history(
                    channel=channel,
                    latest=message_ts,
                    limit=1,
                    inclusive=True
                )
                if result.get("messages"):
                    files = result["messages"][0].get("files", [])
                    logger.info(f"Fetched message, found {len(files)} files")
            except Exception as e:
                logger.warning(f"Failed to fetch message for files: {e}")

        # Check for PDF files
        pdf_files = [f for f in files if f.get("mimetype") == "application/pdf"]

        if pdf_files:
            # Process PDF files for history import
            await handle_pdf_import(client, channel, message_ts, pdf_files)
            return

        # Check if this is a thread reply in CSM response channel
        thread_ts = event.get("thread_ts")
        if channel == settings.csm_response_channel_id and thread_ts:
            # Try to find or reconstruct session
            if thread_ts not in _csm_sessions:
                session = await _reconstruct_session(client, channel, thread_ts)
                if not session:
                    await client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="⚠️ 원본 문의를 찾을 수 없습니다. 원본 문의 내용을 알려주시면 복원해드리겠습니다."
                    )
                    return

            logger.info(f"[APP_MENTION] Routing to CSM thread reply handler for {thread_ts}")
            await handle_csm_thread_reply(
                client=client,
                channel=channel,
                thread_ts=thread_ts,
                message_ts=message_ts,
                text=text,
                user=user
            )
            return

        # Check if this is a CSM channel - handle conversational Q&A
        if settings.is_csm_channel(channel):
            await handle_csm_mention(client, event)
            return

        # Non-CSM channel mention without PDF - ignore
        logger.debug("Non-CSM channel mention without PDF, ignoring")

    @app.action("deliver_to_customer")
    async def on_deliver_to_customer(ack, body, client):
        """Handle deliver to customer button click."""
        await handle_deliver_to_customer(ack, body, client)


async def handle_ticket_emoji(
    client: AsyncWebClient,
    channel: str,
    message_ts: str,
    user: str
):
    """Handle ticket emoji reaction - generate support response and post to CSM channel.

    V2 Flow:
    1. User adds ticket emoji to customer message
    2. Bot generates response
    3. Bot posts response to CSM response channel (not to original thread)
    4. CSM can then review and improve the response

    Args:
        client: Slack client
        channel: Channel ID where ticket was created
        message_ts: Message timestamp
        user: User who added the reaction
    """
    # Check if we can process this ticket
    if not await can_process_ticket(channel, message_ts):
        logger.info(f"Skipping ticket processing for {channel}/{message_ts} - already processed")
        return

    # Check if CSM response channel is configured (required for v2)
    csm_response_channel = settings.csm_response_channel_id
    if not csm_response_channel:
        logger.error("CSM_RESPONSE_CHANNEL_ID not configured - this is required for v2")
        await set_message_state(channel, message_ts, MessageState.IDLE)
        return

    # Set state to processing
    await set_message_state(channel, message_ts, MessageState.PROCESSING)

    try:
        # Get channel info for customer mapping
        channel_name = ""
        try:
            channel_info = await client.conversations_info(channel=channel)
            channel_name = channel_info.get("channel", {}).get("name", "")
            logger.info(f"Channel name: {channel_name}")
        except Exception as e:
            logger.warning(f"Could not get channel info: {e}")

        # Get the original message
        result = await client.conversations_history(
            channel=channel,
            latest=message_ts,
            limit=1,
            inclusive=True
        )

        if not result.get("messages"):
            logger.error(f"Could not find message {message_ts} in {channel}")
            return

        message = result["messages"][0]

        # Detect thread reply vs parent message with thread
        if message.get("ts") != message_ts:
            # Case 1: 댓글에 이모지 → 해당 댓글만 사용
            parent_ts = message.get("ts")
            logger.info(f"Emoji on thread reply, fetching from thread {parent_ts}")
            try:
                thread_result = await client.conversations_replies(
                    channel=channel,
                    ts=parent_ts
                )
                for reply in thread_result.get("messages", []):
                    if reply.get("ts") == message_ts:
                        message = reply
                        logger.info(f"Found thread reply: {message.get('text', '')[:80]}")
                        break
            except Exception as e:
                logger.warning(f"Could not fetch thread reply: {e}")
        elif message.get("reply_count", 0) > 0:
            # Case 2: 부모 메시지에 이모지 + 댓글 있음 → 스레드 전체 수집
            logger.info(f"Emoji on parent message with {message.get('reply_count')} replies, collecting thread")
            try:
                thread_result = await client.conversations_replies(
                    channel=channel,
                    ts=message_ts
                )
                thread_messages = thread_result.get("messages", [])
                # Filter out noise: bot messages, short messages (<15 chars), system-like messages
                meaningful_texts = []
                for m in thread_messages:
                    if m.get("bot_id"):  # Skip bot messages
                        continue
                    text = m.get("text", "").strip()
                    if not text or len(text) < 15:  # Skip empty or very short (e.g. "감사합니다!")
                        continue
                    # Skip ticket system messages
                    if text in ("티켓 전송 완료", "티켓 접수 완료!", "티켓 접수 완료"):
                        continue
                    meaningful_texts.append(text)
                if meaningful_texts:
                    message["text"] = "\n\n".join(meaningful_texts)
                    logger.info(f"Collected {len(meaningful_texts)} meaningful messages from thread (filtered {len(thread_messages) - len(meaningful_texts)} noise)")
            except Exception as e:
                logger.warning(f"Could not fetch thread replies: {e}")
        # Case 3: 일반 메시지 (스레드 없음) → 기존 동작

        user_query = message.get("text", "")
        attachments = message.get("attachments", [])
        files = message.get("files", [])

        if not user_query:
            logger.warning(f"Empty message text for {channel}/{message_ts}")
            return

        logger.info(f"Processing ticket: {user_query[:100]}...")

        # Analyze message content (URLs and images)
        analyzed_content = None
        enhanced_query = user_query
        try:
            from src.utils.content_analyzer import analyze_message_content, extract_search_keywords
            analyzed_content = await analyze_message_content(
                text=user_query,
                attachments=attachments,
                files=files,
                slack_token=settings.slack_bot_token
            )
            enhanced_query = analyzed_content.get('combined_context', user_query)

            if analyzed_content.get('url_contents'):
                logger.info(f"Analyzed {len(analyzed_content['url_contents'])} URLs")
            if analyzed_content.get('image_analyses'):
                logger.info(f"Analyzed {len(analyzed_content['image_analyses'])} images")
        except Exception as e:
            logger.warning(f"Content analysis failed: {e}")

        # Analyze query with LLM for better search
        query_analysis = None
        optimized_query = user_query
        try:
            query_analysis = await analyze_query(user_query)
            optimized_query = query_analysis.get("search_query", user_query)
            logger.info(f"Query intent: {query_analysis.get('intent', 'unknown')}")
            logger.info(f"Optimized query: {optimized_query}")
        except Exception as e:
            logger.warning(f"Query analysis failed: {e}")

        # Search for relevant documents
        # Use multi-query search when query has multiple sub-questions
        sub_questions = query_analysis.get("sub_questions", []) if query_analysis else []
        if len(sub_questions) > 1:
            logger.info(f"[MULTI-QUERY] {len(sub_questions)} sub-questions detected")
            for i, sq in enumerate(sub_questions, 1):
                logger.info(f"  [{i}] {sq.get('question', '')[:60]} → '{sq.get('search_query', '')}'")
            search_results = await multi_query_hybrid_search(
                query_analysis,
                moengage_top_k=5,
                history_top_k=3,
            )
        else:
            search_results = await hybrid_search(
                optimized_query,
                use_llm_optimization=False,
                query_analysis=query_analysis
            )

        # Track pre-rerank counts per source
        from src.knowledge.hybrid_searcher import group_results_by_source
        pre_rerank_by_source = {s: len(items) for s, items in group_results_by_source(search_results).items()}
        logger.info(f"[PRE-RERANK] By source: {pre_rerank_by_source}")

        # Save all results before reranking (so agent can reference filtered-out docs)
        all_search_results_before_rerank = list(search_results)

        # Re-rank results using Claude for relevance
        try:
            search_results = await rerank_results(user_query, search_results)
        except Exception as e:
            logger.warning(f"[RERANK] Failed, using original results: {e}")

        post_rerank_by_source = {s: len(items) for s, items in group_results_by_source(search_results).items()}
        logger.info(f"[POST-RERANK] By source: {post_rerank_by_source}")

        # Format context for LLM (CSM gets full access)
        from src.knowledge.hybrid_searcher import format_context_for_llm
        context = format_context_for_llm(search_results, current_channel_id=None)

        # Retrieve learning from past similar cases
        learning_data = None
        learning_context = ""
        try:
            learning_data = await get_learning_for_query(user_query, top_k=3)
            if learning_data and learning_data.get("has_learning"):
                learning_context = _format_learning_context(learning_data)
                context = f"{context}\n\n{learning_context}"
                logger.info(f"[LEARNING] Injected learning from {len(learning_data.get('similar_queries', []))} similar cases")
            else:
                logger.info("[LEARNING] No similar learning found")
        except Exception as e:
            logger.warning(f"[LEARNING] Retrieval failed: {e}")

        # Count results by source
        moengage_count = sum(1 for r in search_results if r.source == "moengage_docs")
        history_count = sum(1 for r in search_results if r.source == "support_history")

        logger.info(f"[SEARCH RESULTS] Total: {len(search_results)}, MoEngage: {moengage_count}, History: {history_count}")
        for i, r in enumerate(search_results):
            logger.info(f"  [{i+1}] {r.source}: {r.title[:50]}...")

        # Track referenced URLs for later archiving
        referenced_docs = [r.url for r in search_results if r.source == "moengage_docs"]
        referenced_history = [r.url for r in search_results if r.source == "support_history"]

        # Build resource list Block Kit for CSM channel (no answer generation)
        blocks, fallback_text = build_csm_resource_list_blocks(
            search_results=search_results,
            original_query=user_query,
            original_channel=channel,
            original_ts=message_ts,
            channel_name=channel_name,
            ticket_user=user,
            pre_rerank_by_source=pre_rerank_by_source,
            post_rerank_by_source=post_rerank_by_source
        )

        # Post resource list to CSM channel as a new message (not thread)
        try:
            post_result = await client.chat_postMessage(
                channel=csm_response_channel,
                text=fallback_text,
                blocks=blocks
            )
        except Exception as block_err:
            logger.warning(f"Block Kit posting failed, falling back to plain text: {block_err}")
            post_result = await client.chat_postMessage(
                channel=csm_response_channel,
                text=fallback_text
            )
            blocks = None

        csm_thread_ts = post_result.get("ts", "")
        if not csm_thread_ts:
            logger.error("No timestamp returned from chat_postMessage")
            await set_message_state(channel, message_ts, MessageState.IDLE)
            return

        # Store session data for CSM conversation
        async with _session_lock:
            _csm_sessions[csm_thread_ts] = {
                "original_channel": channel,
                "original_ts": message_ts,
                "original_query": user_query,
                "channel_name": channel_name,
                "ticket_user": user,
                "context": context,
                "search_results": search_results,
                "all_search_results": all_search_results_before_rerank,  # Includes filtered-out results
                "initial_response": "",  # No initial response - resource list only
                "iteration_count": 0,
                "feedback": [],
                "improved_responses": [],
                "search_queries": [user_query, optimized_query] if optimized_query != user_query else [user_query],
                "search_results_titles": [r.title for r in search_results],
                "referenced_docs": referenced_docs,
                "referenced_history": referenced_history,
                "created_at": datetime.now().isoformat(),
                "created_at_ts": time.time(),  # For TTL cleanup
                "query_analysis": query_analysis,
                "learning_data": learning_data,
                "learning_context": learning_context,
                "response_messages": {},  # No initial response message
                "delivered_responses": set(),
            }

        # Periodic cleanup of stale sessions
        asyncio.create_task(cleanup_stale_sessions())

        # Create feedback entry for tracking
        feedback_store = get_feedback_store()
        feedback_store.create_entry(
            channel=csm_response_channel,
            message_ts=csm_thread_ts,
            original_query=user_query,
            optimized_query=optimized_query,
            search_results_count=len(search_results),
            moengage_results_count=moengage_count,
            history_results_count=history_count,
            response_length=0,
            metadata={
                "original_channel": channel,
                "original_message_ts": message_ts,
                "query_intent": query_analysis.get("intent") if query_analysis else None,
                "moengage_features": query_analysis.get("moengage_features") if query_analysis else None,
                "channel_name": channel_name,
                "referenced_docs": referenced_docs,
                "referenced_history": referenced_history,
                "is_csm_ticket": True
            }
        )

        # Update state to answered
        await set_message_state(channel, message_ts, MessageState.ANSWERED)

        logger.info(f"Posted CSM ticket response to {csm_response_channel}/{csm_thread_ts}")

    except Exception as e:
        logger.error(f"Error processing ticket: {e}", exc_info=True)
        # Log Slack API error details if available
        if hasattr(e, 'response'):
            logger.error(f"Slack API error response: {getattr(e.response, 'data', 'N/A')}")

        # Post error message to CSM channel
        error_response = format_error_response(str(e))
        try:
            await client.chat_postMessage(
                channel=csm_response_channel,
                text=f"⚠️ 티켓 처리 오류 (원본: {channel}/{message_ts})\n\n{error_response}"
            )
        except Exception as post_error:
            logger.error(f"Failed to post error message: {post_error}")

        # Reset state
        await set_message_state(channel, message_ts, MessageState.IDLE)


async def handle_csm_mention(
    client: AsyncWebClient,
    event: Dict[str, Any]
):
    """Handle @mention in CSM channel - conversational Q&A.

    Args:
        client: Slack client
        event: The app_mention event
    """
    channel = event.get("channel", "")
    message_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts", message_ts)  # If in thread, use thread_ts
    text = event.get("text", "")
    user = event.get("user", "")

    # Remove bot mention to extract actual query
    user_query = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

    if not user_query:
        logger.debug("Empty query after removing bot mention")
        return

    # Check for /history command
    if user_query.startswith('/history'):
        logger.info(f"[CSM MENTION] Processing history command: {user_query[:100]}...")
        await handle_history_command(client, event, user_query)
        return

    logger.info(f"[CSM MENTION] Processing query: {user_query[:100]}...")

    try:
        # Get channel info
        channel_name = ""
        try:
            channel_info = await client.conversations_info(channel=channel)
            channel_name = channel_info.get("channel", {}).get("name", "")
        except Exception as e:
            logger.warning(f"Could not get channel info: {e}")

        # Collect conversation context if in a thread
        conversation_context = ""
        if thread_ts != message_ts:
            try:
                thread_result = await client.conversations_replies(
                    channel=channel,
                    ts=thread_ts
                )
                thread_messages = thread_result.get("messages", [])
                conversation_context = _format_thread_context(thread_messages, message_ts)
                logger.info(f"Collected {len(thread_messages)} thread messages for context")
            except Exception as e:
                logger.warning(f"Could not get thread context: {e}")

        # Analyze query with LLM
        query_analysis = None
        try:
            query_analysis = await analyze_query(user_query)
            logger.info(f"CSM query intent: {query_analysis.get('intent', 'unknown')}")
        except Exception as e:
            logger.warning(f"Query analysis failed: {e}")

        # Hybrid search (CSM gets access to all history URLs)
        search_results = await hybrid_search(user_query)

        # Format context - CSM channel gets full URL access (current_channel_id=None)
        context = format_context_for_llm(search_results, current_channel_id=None)

        # Log search results
        moengage_count = sum(1 for r in search_results if r.source == "moengage_docs")
        history_count = sum(1 for r in search_results if r.source == "support_history")
        logger.info(f"[CSM SEARCH] Total: {len(search_results)}, MoEngage: {moengage_count}, History: {history_count}")

        # Build full context including conversation history
        full_context = context
        if conversation_context:
            full_context = f"## 이전 대화 내용\n{conversation_context}\n\n## 검색된 문서\n{context}"

        # Generate conversational response with Claude (CSM uses conversational style)
        response = await generate_csm_response(full_context, user_query)

        # CSM responses are already conversational, no need for strict grounding validation
        # or template formatting - just use the response directly
        formatted_response = response

        # Post response in thread
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=formatted_response
        )

        logger.info(f"[CSM MENTION] Posted response in {channel}/{thread_ts}")

    except Exception as e:
        logger.error(f"Error processing CSM mention: {e}", exc_info=True)

        # Post error message
        error_response = format_error_response(str(e))
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=error_response
            )
        except Exception as post_error:
            logger.error(f"Failed to post error message: {post_error}")


def _format_thread_context(messages: list, current_ts: str) -> str:
    """Format thread messages as context for the LLM.

    Args:
        messages: List of thread messages
        current_ts: Current message timestamp to exclude

    Returns:
        Formatted context string
    """
    context_parts = []

    for msg in messages:
        # Skip the current message
        if msg.get("ts") == current_ts:
            continue

        text = msg.get("text", "")
        if not text:
            continue

        # Remove bot mentions from text
        text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

        if msg.get("bot_id"):
            context_parts.append(f"[Bot 응답]\n{text[:500]}")
        else:
            context_parts.append(f"[사용자 질문]\n{text[:500]}")

    return "\n\n".join(context_parts[-5:])  # Keep last 5 messages for context


def _format_learning_context(learning_data: dict) -> str:
    """Format learning data as context section for LLM.

    Args:
        learning_data: Dictionary with learning lessons from similar cases

    Returns:
        Formatted learning context string
    """
    sections = ["## 유사 사례에서의 학습"]

    # 유사 사례 원본 질문 (맥락 제공)
    if learning_data.get("similar_queries"):
        sections.append("\n**유사 과거 사례:**")
        for sq in learning_data["similar_queries"][:2]:
            sections.append(f"- 질문: {sq['query']}")

    # 답변 작성 교훈 (가장 중요)
    if learning_data.get("response_lessons"):
        sections.append("\n**답변 작성 시 참고:**")
        for lesson in learning_data["response_lessons"][:2]:
            sections.append(f"- {lesson['lesson']}")

    # 문의 해석 교훈
    if learning_data.get("query_lessons"):
        sections.append("\n**문의 해석 시 참고:**")
        for lesson in learning_data["query_lessons"][:2]:
            sections.append(f"- {lesson['lesson']}")

    # 검색 전략 교훈
    if learning_data.get("search_lessons"):
        sections.append("\n**검색 전략 참고:**")
        for lesson in learning_data["search_lessons"][:2]:
            sections.append(f"- {lesson['lesson']}")

    return "\n".join(sections)


async def handle_csm_thread_reply(
    client: AsyncWebClient,
    channel: str,
    thread_ts: str,
    message_ts: str,
    text: str,
    user: str
):
    """Handle CSM replies in bot response threads for iterative improvement.

    This is called when a CSM replies to a bot response thread in the CSM channel.
    The bot will analyze the CSM's feedback and generate an improved response.

    Args:
        client: Slack client
        channel: CSM response channel ID
        thread_ts: Thread timestamp (original bot response)
        message_ts: Current message timestamp
        text: CSM's message text
        user: CSM user ID
    """
    # Check if we have an active session for this thread
    session = _csm_sessions.get(thread_ts)
    if not session:
        logger.debug(f"No active session for thread {thread_ts}")
        return

    # Remove bot mention if present
    csm_message = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

    if not csm_message:
        return

    logger.info(f"[CSM REPLY] Processing feedback: {csm_message[:100]}...")

    try:
        # Process CSM's natural language request via agent
        session_context = _format_session_context(session)
        claude = get_claude_client()
        analysis = await claude.process_csm_request(csm_message, session_context)

        action = analysis.get("action", "answer")
        logger.info(f"CSM agent action: {action}")

        # Update session with feedback (thread-safe)
        async with _session_lock:
            session["feedback"].append(csm_message)
            session["iteration_count"] += 1

        # Handle different actions
        context = session.get("context", "")

        if action == "search":
            # Search for more resources and post as a resource list
            keywords = analysis.get("keywords", [])
            if not keywords:
                keywords = [csm_message]
            logger.info(f"Additional search requested: {keywords}")

            all_new_results = []
            for keyword in keywords:
                new_results = await hybrid_search(keyword)
                if new_results:
                    all_new_results.extend(new_results)
                    new_context = format_context_for_llm(new_results, current_channel_id=None)
                    context = f"{context}\n\n## 추가 검색 결과 ({keyword})\n{new_context}"
                    async with _session_lock:
                        session["search_queries"].append(keyword)
                        session["search_results_titles"].extend([r.title for r in new_results])
                        session["context"] = context
                        existing = session.get("search_results", [])
                        session["search_results"] = existing + new_results

            if all_new_results:
                try:
                    all_new_results = await rerank_results(
                        session["original_query"], all_new_results
                    )
                except Exception as e:
                    logger.warning(f"[RERANK] Additional search rerank failed: {e}")

                add_blocks, add_fallback = build_csm_resource_list_blocks(
                    search_results=all_new_results,
                    original_query=session["original_query"],
                    original_channel=session["original_channel"],
                    original_ts=session["original_ts"],
                    channel_name=session.get("channel_name", ""),
                    ticket_user=""
                )
                if add_blocks and add_blocks[0].get("type") == "section":
                    add_blocks[0]["text"]["text"] = f"🔍 *추가 검색 결과* ({', '.join(keywords)})"

                try:
                    await client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=add_fallback,
                        blocks=add_blocks
                    )
                except Exception as block_err:
                    logger.warning(f"Block Kit failed for additional search, falling back: {block_err}")
                    await client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=add_fallback
                    )
            else:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"🔍 `{', '.join(keywords)}` 키워드로 검색했지만 관련 자료를 찾지 못했습니다."
                )
            return

        elif action == "respond":
            # Generate a customer response based on accumulated search results
            logger.info("[CSM REPLY] Writing customer response based on resources")

            all_results = session.get("search_results", [])
            write_context = format_context_for_llm(all_results, current_channel_id=None)

            # Append learning context from similar past cases
            lc = session.get("learning_context", "")
            if lc:
                write_context = f"{write_context}\n\n{lc}"

            from src.llm.prompts import get_write_response_system_prompt, get_write_response_prompt

            csm_instruction = analysis.get("instruction", "")
            # Pass sub_questions from query analysis for numbered Q→A mapping
            qa = session.get("query_analysis") or {}
            sub_questions = qa.get("sub_questions", [])
            prompt = get_write_response_prompt(
                context=write_context,
                original_query=session["original_query"],
                csm_instruction=csm_instruction,
                sub_questions=sub_questions
            )

            written_response = await claude.async_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=3000,
                system=get_write_response_system_prompt(),
                messages=[{"role": "user", "content": prompt}]
            )
            written_text = written_response.content[0].text if written_response.content else ""

            async with _session_lock:
                session["improved_responses"].append(written_text)
                session["context"] = write_context

            iteration = session["iteration_count"]
            button_value = json.dumps({"csm_thread_ts": thread_ts})
            blocks, fallback_text = build_improved_response_blocks(
                response=written_text,
                iteration=iteration,
                search_results=None,
                button_value=button_value,
                ticket_user=session.get("ticket_user", "")
            )

            try:
                post_result = await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=fallback_text,
                    blocks=blocks
                )
            except Exception as block_err:
                logger.warning(f"Block Kit failed for write_response, falling back: {block_err}")
                post_result = await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=fallback_text
                )
                blocks = None

            response_msg_ts = post_result.get("ts", "")
            if response_msg_ts:
                async with _session_lock:
                    if "response_messages" not in session:
                        session["response_messages"] = {}
                    session["response_messages"][response_msg_ts] = written_text

            logger.info(f"[CSM REPLY] Posted written response #{iteration}")

            # Auto-extract learning if CSM gave feedback (iteration >= 1)
            if iteration >= 1 and not session.get("auto_learned_at"):
                asyncio.create_task(_auto_extract_learning(session, thread_ts))

            return

        else:
            # answer action — CSM 질문에 직접 답변
            answer_text = analysis.get("message", "")
            if not answer_text:
                answer_text = "요청을 처리할 수 없습니다. 다시 말씀해주세요."
            logger.info(f"[CSM REPLY] Direct answer to CSM ({len(answer_text)} chars)")
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=answer_text
            )
            return

    except Exception as e:
        logger.error(f"Error processing CSM reply: {e}", exc_info=True)

        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"⚠️ 개선된 답변 생성 중 오류가 발생했습니다: {str(e)[:100]}"
        )


async def handle_deliver_to_customer(ack, body, client):
    """Handle 'deliver_to_customer' button click.

    Delivers the bot's response to the original customer thread.
    """
    await ack()

    message = body.get("message", {})
    msg_ts = message.get("ts", "")
    csm_thread_ts = message.get("thread_ts") or msg_ts
    original_blocks = message.get("blocks", [])
    csm_channel = body.get("channel", {}).get("id", "")
    user_id = body.get("user", {}).get("id", "")

    # Look up session
    session = _csm_sessions.get(csm_thread_ts)

    if not session:
        await client.chat_postEphemeral(
            channel=csm_channel,
            user=user_id,
            text="⚠️ 세션이 만료되었습니다. 답변을 복사하여 직접 전달해주세요."
        )
        return

    # Check for double-delivery
    delivered = session.get("delivered_responses", set())
    if msg_ts in delivered:
        await client.chat_postEphemeral(
            channel=csm_channel,
            user=user_id,
            text="이미 전달된 답변입니다."
        )
        return

    # Get the response text to deliver
    response_messages = session.get("response_messages", {})
    response_text = response_messages.get(msg_ts, "")

    if not response_text:
        # Fallback: use the latest available response
        if session.get("improved_responses"):
            response_text = session["improved_responses"][-1]
        else:
            response_text = session.get("initial_response", "")

    if not response_text:
        await client.chat_postEphemeral(
            channel=csm_channel,
            user=user_id,
            text="⚠️ 전달할 답변 내용을 찾을 수 없습니다."
        )
        return

    original_channel = session.get("original_channel", "")
    original_ts = session.get("original_ts", "")

    if not original_channel or not original_ts:
        await client.chat_postEphemeral(
            channel=csm_channel,
            user=user_id,
            text="⚠️ 원본 메시지 정보를 찾을 수 없습니다."
        )
        return

    try:
        # Format for customer (answer + source links)
        search_results = session.get("search_results", [])
        customer_response = format_customer_response(
            response_text, search_results, current_channel_id=original_channel
        )

        # Post to customer thread
        customer_post = await client.chat_postMessage(
            channel=original_channel,
            thread_ts=original_ts,
            text=customer_response
        )

        # Build customer thread URL for confirmation
        customer_msg_ts = customer_post.get("ts", "")
        customer_thread_url = (
            f"https://slack.com/archives/{original_channel}"
            f"/p{customer_msg_ts.replace('.', '')}"
        )

        # Update CSM message: remove button, add confirmation
        confirmed_blocks = build_delivered_confirmation_blocks(
            original_blocks, customer_thread_url
        )
        await client.chat_update(
            channel=csm_channel,
            ts=msg_ts,
            text=f":완료: 전달 완료 | {customer_thread_url}",
            blocks=confirmed_blocks
        )

        # Mark as delivered in session
        async with _session_lock:
            if "delivered_responses" not in session:
                session["delivered_responses"] = set()
            session["delivered_responses"].add(msg_ts)

        # Update state machine
        await set_message_state(original_channel, original_ts, MessageState.DELIVERED)

        logger.info(
            f"[DELIVER] Response delivered to {original_channel}/{original_ts} "
            f"from CSM thread {csm_thread_ts}"
        )

    except Exception as e:
        logger.error(f"Error delivering response: {e}", exc_info=True)
        await client.chat_postEphemeral(
            channel=csm_channel,
            user=user_id,
            text=f"⚠️ 전달 중 오류가 발생했습니다: {str(e)[:100]}"
        )


def _format_session_context(session: Dict[str, Any]) -> str:
    """Format session data as rich context for CSM agent.

    Args:
        session: CSM session data

    Returns:
        Formatted context string including search results details
    """
    parts = []

    parts.append(f"[원본 고객 문의]\n{session['original_query'][:500]}")

    # Include post-rerank results (shown to CSM)
    search_results = session.get("search_results", [])
    if search_results:
        results_lines = []
        for i, r in enumerate(search_results, 1):
            source = getattr(r, 'source', 'unknown')
            title = getattr(r, 'title', 'Untitled')
            url = getattr(r, 'url', '')
            snippet = getattr(r, 'snippet', '')[:200]
            results_lines.append(f"  {i}. [{source}] {title}\n     URL: {url}\n     요약: {snippet}")
        parts.append(f"[관련성 높은 자료 (CSM에게 표시됨, {len(search_results)}건)]\n" + "\n".join(results_lines))

    # Include pre-rerank results (filtered out but may be referenced)
    all_results = session.get("all_search_results", [])
    filtered_out = [r for r in all_results if r not in search_results]
    if filtered_out:
        filtered_lines = []
        for i, r in enumerate(filtered_out, 1):
            source = getattr(r, 'source', 'unknown')
            title = getattr(r, 'title', 'Untitled')
            url = getattr(r, 'url', '')
            filtered_lines.append(f"  {i}. [{source}] {title}\n     URL: {url}")
        parts.append(f"[관련성 낮아 제외된 자료 ({len(filtered_out)}건)]\n" + "\n".join(filtered_lines))

    # Include initial response if exists
    initial = session.get('initial_response', '')
    if initial:
        parts.append(f"[초기 답변]\n{initial[:500]}")

    # Include feedback and improved responses
    for i, (fb, resp) in enumerate(zip(session.get("feedback", []), session.get("improved_responses", []))):
        parts.append(f"[CSM 피드백 #{i+1}]\n{fb[:300]}")
        parts.append(f"[개선 답변 #{i+1}]\n{resp[:300]}")

    return "\n\n".join(parts)


async def handle_complete_emoji(
    client: AsyncWebClient,
    channel: str,
    message_ts: str,
    user: str
):
    """Handle complete emoji reaction - archive to history with learning extraction.

    V2 Enhancement:
    - For CSM response channel threads, extract learning points from the improvement conversation
    - Save both history entry and learning entry

    Args:
        client: Slack client
        channel: Channel ID
        message_ts: Message timestamp
        user: User who added the reaction
    """
    # Check if we can process completion
    if not await can_process_complete(channel, message_ts):
        logger.info(f"Skipping complete processing for {channel}/{message_ts}")
        return

    # Set state to archiving
    await set_message_state(channel, message_ts, MessageState.ARCHIVING)

    try:
        # Check if this is the CSM response channel with an active session
        is_csm_response_channel = channel == settings.csm_response_channel_id
        session = None
        csm_thread_ts = None  # Preserve for completion notification

        if is_csm_response_channel:
            # Direct lookup by CSM thread ts
            session = _csm_sessions.get(message_ts)
            if session:
                csm_thread_ts = message_ts
        else:
            # Search for session by original_channel + original_ts
            # (when :완료: is added on the original customer message)
            for csm_ts, s in _csm_sessions.items():
                if s.get("original_channel") == channel and s.get("original_ts") == message_ts:
                    session = s
                    csm_thread_ts = csm_ts
                    logger.info(f"Found CSM session via original message: csm_thread={csm_ts}")
                    break

        # Check if this is a CSM channel (for legacy support)
        is_csm = settings.is_csm_channel(channel) or is_csm_response_channel

        # Get channel info for customer mapping
        channel_name = ""
        customer = "진에어"  # Default customer
        try:
            channel_info = await client.conversations_info(channel=channel)
            channel_name = channel_info.get("channel", {}).get("name", "")
            # For CSM channels, use configured customer name
            if is_csm:
                customer = settings.csm_default_customer
            elif channel_name:
                # Extract customer name from channel name (e.g., "jinair-support" -> "진에어")
                customer = channel_name.split("-")[0] if "-" in channel_name else customer
            logger.info(f"Channel: {channel_name}, Customer: {customer}, is_csm: {is_csm}")
        except Exception as e:
            logger.warning(f"Could not get channel info: {e}")

        # Get CSM user info
        csm_metadata = {}
        try:
            user_info = await client.users_info(user=user)
            user_data = user_info.get("user", {})
            csm_metadata = {
                "csm_user_id": user,
                "csm_user_name": user_data.get("name", ""),
                "csm_real_name": user_data.get("real_name", ""),
            }
            logger.info(f"CSM user: {csm_metadata.get('csm_real_name', user)}")
        except Exception as e:
            logger.warning(f"Could not get CSM user info: {e}")
            csm_metadata = {"csm_user_id": user}

        # Get the entire thread
        result = await client.conversations_replies(
            channel=channel,
            ts=message_ts
        )

        messages = result.get("messages", [])

        if not messages:
            logger.warning(f"No messages found in thread {channel}/{message_ts}")
            return

        logger.info(f"Archiving thread with {len(messages)} messages")

        # Generate thread URL
        thread_url = f"https://slack.com/archives/{channel}/p{message_ts.replace('.', '')}"

        # Get referenced docs from session or feedback store
        referenced_docs = []
        referenced_history = []

        if session:
            referenced_docs = session.get("referenced_docs", [])
            referenced_history = session.get("referenced_history", [])
        else:
            try:
                feedback_store = get_feedback_store()
                for msg in messages:
                    if msg.get("bot_id"):
                        bot_ts = msg.get("ts", "")
                        entry = feedback_store.get_entry(channel, bot_ts)
                        if entry and entry.metadata:
                            referenced_docs = entry.metadata.get("referenced_docs", [])
                            referenced_history = entry.metadata.get("referenced_history", [])
                            break
            except Exception as e:
                logger.warning(f"Could not get referenced docs from feedback store: {e}")

        # Add to history
        entry_id = await add_from_slack_thread(
            thread_messages=messages,
            customer=customer,
            thread_url=thread_url,
            channel_id=channel,
            channel_name=channel_name,
            referenced_docs=referenced_docs,
            referenced_history=referenced_history,
            source_type="csm" if is_csm else "customer",
            csm_metadata=csm_metadata
        )

        # V2: Extract and save learning points from conversation
        learning_entry_id = None
        learning_points_dict = {}

        # Extract original query from first message
        original_query = messages[0].get("text", "") if messages else ""

        if session and session.get("iteration_count", 0) > 0:
            # Case 1: Session exists with improvements - use session data
            logger.info(f"Extracting learning points from {session['iteration_count']} iterations")

            # Get final response
            final_response = (
                session["improved_responses"][-1]
                if session["improved_responses"]
                else session["initial_response"]
            )
            original_query = session["original_query"]

            # Extract learning points using Claude
            try:
                learning_points_dict = await extract_learning_points(
                    original_query=session["original_query"],
                    initial_response=session["initial_response"],
                    csm_feedback=session.get("feedback", []),
                    improved_responses=session.get("improved_responses", []),
                    final_response=final_response
                )
                logger.info(f"Extracted learning points from session: {learning_points_dict}")
            except Exception as e:
                logger.warning(f"Failed to extract learning points from session: {e}")
                learning_points_dict = {}

            # Clean up session (thread-safe)
            async with _session_lock:
                if message_ts in _csm_sessions:
                    del _csm_sessions[message_ts]
        else:
            # Case 2: No session or no improvements - extract from thread messages
            logger.info(f"Extracting learning points from thread messages ({len(messages)} messages)")

            try:
                learning_points_dict = await extract_learning_from_thread(messages)
                logger.info(f"Extracted learning points from thread: {learning_points_dict}")
            except Exception as e:
                logger.warning(f"Failed to extract learning points from thread: {e}")
                learning_points_dict = {}

        # Get responses from messages for learning entry (moved up for iteration check)
        bot_responses = [m.get("text", "") for m in messages if m.get("bot_id")]
        user_messages = [m.get("text", "") for m in messages if not m.get("bot_id")]

        # Create and save LearningEntry if we have learning content or CSM feedback
        iteration_count = session.get("iteration_count", 0) if session else max(0, len(bot_responses) - 1)
        has_learning_content = any([
            learning_points_dict.get("query_lesson"),
            learning_points_dict.get("search_lesson"),
            learning_points_dict.get("response_lesson"),
            iteration_count >= 1,  # CSM이 최소 1회 피드백을 준 경우
        ])

        if has_learning_content:

            initial_response = bot_responses[0] if bot_responses else ""
            final_response = bot_responses[-1] if bot_responses else ""

            # Use session data if available, otherwise use parsed messages
            if session:
                initial_response = session.get("initial_response", initial_response)
                final_response = (
                    session["improved_responses"][-1]
                    if session.get("improved_responses")
                    else session.get("initial_response", final_response)
                )

            learning_entry = LearningEntry(
                original_query=original_query,
                original_channel=session.get("original_channel", channel) if session else channel,
                original_ts=session.get("original_ts", message_ts) if session else message_ts,
                csm_thread_channel=channel,
                csm_thread_ts=message_ts,
                query_interpretation=QueryInterpretation(
                    initial=original_query,
                    corrections=user_messages[1:4] if len(user_messages) > 1 else [],
                    final=original_query,
                ),
                search_history=SearchHistory(
                    initial_queries=session.get("search_queries", [original_query])[:2] if session else [original_query],
                    initial_results=session.get("search_results_titles", [])[:5] if session else [],
                    additional_searches=[
                        SearchIteration(query=q, results=[])
                        for q in (session.get("search_queries", [])[2:] if session else [])
                    ],
                    used_documents=referenced_docs[:5],
                ),
                response_evolution=ResponseEvolution(
                    initial_response=initial_response,
                    feedback=session.get("feedback", user_messages[1:]) if session else user_messages[1:],
                    iterations=session.get("improved_responses", bot_responses[1:]) if session else bot_responses[1:],
                    final_response=final_response,
                ),
                learning_points=LearningPoints(
                    query_lesson=learning_points_dict.get("query_lesson", ""),
                    search_lesson=learning_points_dict.get("search_lesson", ""),
                    response_lesson=learning_points_dict.get("response_lesson", ""),
                ),
                customer=customer,
                category=learning_points_dict.get("category", "기타"),
                created_at=session.get("created_at", datetime.now().isoformat()) if session else datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                csm_user_id=csm_metadata.get("csm_user_id", ""),
                csm_user_name=csm_metadata.get("csm_real_name", ""),
                iteration_count=session.get("iteration_count", 0) if session else len(bot_responses) - 1,
            )

            # Save learning entry
            try:
                learning_entry_id = await save_learning_entry(learning_entry)
                logger.info(f"Saved learning entry: {learning_entry_id}")
            except Exception as e:
                logger.error(f"Failed to save learning entry: {e}")
        else:
            logger.info("No learning content extracted from conversation")

        # Update state
        if entry_id:
            await set_message_state(channel, message_ts, MessageState.COMPLETED)
            logger.info(f"Archived thread to history: {entry_id}, learning: {learning_entry_id}")

            # Send completion notification to CSM channel thread
            if csm_thread_ts and settings.csm_response_channel_id:
                try:
                    summary_parts = [
                        ":완료: *history와 학습내용이 저장되었습니다.*\n",
                        f"*저장 요약*",
                        f"- 히스토리 ID: `{entry_id[:8]}...`",
                    ]
                    if learning_entry_id:
                        summary_parts.append(f"- 학습 ID: `{learning_entry_id[:8]}...`")
                    summary_parts.append(f"- 피드백 횟수: {iteration_count}회")

                    if learning_points_dict:
                        summary_parts.append("- 학습 포인트:")
                        if learning_points_dict.get("query_lesson"):
                            summary_parts.append(f"  • 문의 해석: {learning_points_dict['query_lesson'][:80]}")
                        if learning_points_dict.get("search_lesson"):
                            summary_parts.append(f"  • 검색 전략: {learning_points_dict['search_lesson'][:80]}")
                        if learning_points_dict.get("response_lesson"):
                            summary_parts.append(f"  • 답변 작성: {learning_points_dict['response_lesson'][:80]}")

                    await client.chat_postMessage(
                        channel=settings.csm_response_channel_id,
                        thread_ts=csm_thread_ts,
                        text="\n".join(summary_parts)
                    )
                    logger.info(f"Posted completion summary to CSM thread {csm_thread_ts}")
                except Exception as notify_err:
                    logger.warning(f"Failed to post completion notification: {notify_err}")
        else:
            logger.warning(f"Failed to archive thread {channel}/{message_ts}")
            await set_message_state(channel, message_ts, MessageState.ANSWERED)

    except Exception as e:
        logger.error(f"Error archiving thread: {e}", exc_info=True)
        await set_message_state(channel, message_ts, MessageState.ANSWERED)


async def handle_feedback_emoji(
    client: AsyncWebClient,
    channel: str,
    message_ts: str,
    user: str,
    feedback_type: str
):
    """Handle feedback emoji reaction - record user feedback.

    Args:
        client: Slack client
        channel: Channel ID
        message_ts: Message timestamp (of the bot's response)
        user: User who added the reaction
        feedback_type: "positive" or "negative"
    """
    feedback_store = get_feedback_store()

    # Try to add feedback to the entry
    success = feedback_store.add_feedback(
        channel=channel,
        message_ts=message_ts,
        feedback=feedback_type,
        user=user
    )

    if success:
        logger.info(f"Recorded {feedback_type} feedback for {channel}/{message_ts} by {user}")

        # Get statistics
        stats = feedback_store.get_statistics()
        logger.info(
            f"Feedback stats: {stats['positive_feedback']} positive, "
            f"{stats['negative_feedback']} negative, "
            f"rate: {stats['positive_rate']:.1%}"
        )

        # If negative feedback, log for analysis
        if feedback_type == "negative":
            entry = feedback_store.get_entry(channel, message_ts)
            if entry:
                logger.warning(
                    f"Negative feedback received for query: '{entry.original_query[:100]}...', "
                    f"optimized: '{entry.optimized_query}', "
                    f"results: {entry.search_results_count}"
                )
    else:
        # Entry not found - this might be feedback on a message we didn't track
        logger.debug(f"No feedback entry found for {channel}/{message_ts}")


async def handle_pdf_import(
    client: AsyncWebClient,
    channel: str,
    message_ts: str,
    pdf_files: list
):
    """Handle PDF file import from Slack message.

    Args:
        client: Slack client
        channel: Channel ID
        message_ts: Message timestamp
        pdf_files: List of PDF file dictionaries from Slack
    """
    if not PDF_IMPORTER_AVAILABLE:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=message_ts,
            text="PDF 임포트 기능을 사용할 수 없습니다. pdfplumber 패키지가 설치되어 있는지 확인해주세요."
        )
        return

    # Send processing message
    processing_msg = await client.chat_postMessage(
        channel=channel,
        thread_ts=message_ts,
        text=f"PDF 파일 {len(pdf_files)}개를 처리 중입니다..."
    )

    results = []
    errors = []

    for pdf_file in pdf_files:
        filename = pdf_file.get("name", "unknown.pdf")
        url = pdf_file.get("url_private")

        if not url:
            errors.append(f"{filename}: URL을 찾을 수 없음")
            continue

        try:
            # Download PDF from Slack
            pdf_bytes = await download_slack_file(client, url, settings.slack_bot_token)

            if not pdf_bytes:
                errors.append(f"{filename}: 다운로드 실패")
                continue

            logger.info(f"Downloaded PDF: {filename} ({len(pdf_bytes)} bytes)")

            # Import PDF
            importer = PDFHistoryImporter()
            entry_id = await importer.import_from_bytes(pdf_bytes, filename)

            if entry_id:
                results.append(f"{filename} -> ID: {entry_id[:8]}...")
            else:
                errors.append(f"{filename}: 임포트 실패")

        except Exception as e:
            logger.error(f"Error importing PDF {filename}: {e}", exc_info=True)
            errors.append(f"{filename}: {str(e)[:50]}")

    # Build response message
    response_parts = []

    if results:
        response_parts.append("**임포트 완료**")
        for r in results:
            response_parts.append(f"- {r}")

    if errors:
        response_parts.append("\n**오류**")
        for e in errors:
            response_parts.append(f"- {e}")

    response_text = "\n".join(response_parts) if response_parts else "처리할 PDF 파일이 없습니다."

    # Update processing message
    await client.chat_update(
        channel=channel,
        ts=processing_msg["ts"],
        text=response_text
    )
