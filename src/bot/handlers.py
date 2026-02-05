"""Slack event handlers for emoji reactions and app mentions."""

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
    format_learning_saved_confirmation
)
from src.knowledge.hybrid_searcher import search_and_format, hybrid_search, format_context_for_llm
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
from src.knowledge.learning_store import get_learning_store, save_learning_entry
from src.llm.claude_client import (
    generate_response,
    generate_csm_response,
    extract_learning_points,
    extract_learning_from_thread,
    analyze_csm_reply,
    generate_improved_response
)
from src.llm.thread_analyzer import analyze_slack_thread, detect_resolution_keywords
from src.llm.grounding_validator import validate_and_filter_response
from src.llm.query_optimizer import analyze_query
from src.utils.logger import logger
from src.bot.history_command import handle_history_command

# In-memory store for active CSM ticket sessions
# Maps: csm_thread_ts -> session_data
_csm_sessions: Dict[str, Dict[str, Any]] = {}
_session_lock = asyncio.Lock()

# Session TTL in seconds (24 hours)
SESSION_TTL_SECONDS = 86400


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

        # Handle ticket emoji
        if reaction == settings.ticket_emoji:
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

        # Check if this is a reply in the CSM response channel
        if channel == settings.csm_response_channel_id:
            # Check if the thread has an active session
            if thread_ts in _csm_sessions:
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

        # Check if this is a CSM channel - handle conversational Q&A
        if settings.is_csm_channel(channel):
            await handle_csm_mention(client, event)
            return

        # Non-CSM channel mention without PDF - ignore
        logger.debug("Non-CSM channel mention without PDF, ignoring")


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

        # Search for relevant documents (with pre-analyzed query)
        search_results = await hybrid_search(
            user_query,
            use_llm_optimization=False  # Already optimized above
        )

        # Also search with optimized query if different
        if optimized_query != user_query:
            from src.knowledge.moengage_api import search_moengage
            try:
                optimized_results = await search_moengage(optimized_query)
                # Merge results (avoid duplicates)
                existing_urls = {r.url for r in search_results}
                for r in optimized_results:
                    if r.url not in existing_urls:
                        from src.knowledge.hybrid_searcher import UnifiedSearchResult
                        search_results.append(UnifiedSearchResult(
                            title=r.title,
                            url=r.url,
                            content=r.content,
                            snippet=r.snippet,
                            source="moengage_docs",
                            score=0.85
                        ))
            except Exception as e:
                logger.warning(f"Optimized search failed: {e}")

        # Format context for LLM (CSM gets full access)
        from src.knowledge.hybrid_searcher import format_context_for_llm
        context = format_context_for_llm(search_results, current_channel_id=None)

        # Count results by source
        moengage_count = sum(1 for r in search_results if r.source == "moengage_docs")
        history_count = sum(1 for r in search_results if r.source == "support_history")

        logger.info(f"[SEARCH RESULTS] Total: {len(search_results)}, MoEngage: {moengage_count}, History: {history_count}")
        for i, r in enumerate(search_results):
            logger.info(f"  [{i+1}] {r.source}: {r.title[:50]}...")

        # Track referenced URLs for later archiving
        referenced_docs = [r.url for r in search_results if r.source == "moengage_docs"]
        referenced_history = [r.url for r in search_results if r.source == "support_history"]

        # Generate response with Claude (use enhanced query with URL/image context)
        response = await generate_response(context, enhanced_query if analyzed_content else user_query)

        # Validate grounding
        final_response, was_modified = await validate_and_filter_response(
            context, response, strict=False
        )

        # Format the response for CSM channel (includes original query link)
        formatted_response = format_csm_ticket_response(
            final_response,
            user_query,
            channel,
            message_ts,
            search_results,
            was_modified,
            channel_name=channel_name
        )

        # Post response to CSM channel as a new message (not thread)
        post_result = await client.chat_postMessage(
            channel=csm_response_channel,
            text=formatted_response
        )

        # Validate post result
        if not post_result.get("ok", True):  # Slack SDK may not include 'ok' on success
            error_msg = post_result.get("error", "Unknown error")
            logger.error(f"Failed to post to CSM channel: {error_msg}")
            await set_message_state(channel, message_ts, MessageState.IDLE)
            return

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
                "context": context,
                "search_results": search_results,
                "initial_response": final_response,
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
            response_length=len(final_response),
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
        # Analyze CSM's intent
        conversation_context = _format_session_context(session)
        analysis = await analyze_csm_reply(csm_message, conversation_context)

        intent = analysis.get("intent", "other")
        logger.info(f"CSM intent: {intent}")

        # Update session with feedback (thread-safe)
        async with _session_lock:
            session["feedback"].append(csm_message)
            session["iteration_count"] += 1

        # Handle different intents
        if intent == "approval":
            # CSM approves the response - no need to generate new response
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="✓ 확인했습니다. 원본 메시지에 ✅ 이모지를 추가하시면 히스토리에 저장됩니다."
            )
            return

        # For other intents, generate improved response
        context = session.get("context", "")
        additional_context = analysis.get("context", "")

        # If additional search is requested, do it
        if intent == "additional_search" and analysis.get("keywords"):
            keywords = analysis.get("keywords", [])
            logger.info(f"Additional search requested: {keywords}")

            for keyword in keywords:
                new_results = await hybrid_search(keyword)
                if new_results:
                    # Add new context
                    new_context = format_context_for_llm(new_results, current_channel_id=None)
                    context = f"{context}\n\n## 추가 검색 결과 ({keyword})\n{new_context}"

                    # Track search
                    session["search_queries"].append(keyword)
                    session["search_results_titles"].extend([r.title for r in new_results])

        # Get previous response (either initial or last improved)
        previous_response = (
            session["improved_responses"][-1]
            if session["improved_responses"]
            else session["initial_response"]
        )

        # Generate improved response
        improved_response = await generate_improved_response(
            context=context,
            original_query=session["original_query"],
            previous_response=previous_response,
            csm_feedback=csm_message,
            additional_context=additional_context
        )

        # Store improved response (thread-safe)
        async with _session_lock:
            session["improved_responses"].append(improved_response)

        # Format and post the improved response
        iteration = session["iteration_count"]
        formatted_response = format_improved_response(
            improved_response,
            iteration,
            search_results=None  # Already included in the response
        )

        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=formatted_response
        )

        logger.info(f"[CSM REPLY] Posted improved response #{iteration}")

    except Exception as e:
        logger.error(f"Error processing CSM reply: {e}", exc_info=True)

        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"⚠️ 개선된 답변 생성 중 오류가 발생했습니다: {str(e)[:100]}"
        )


def _format_session_context(session: Dict[str, Any]) -> str:
    """Format session data as conversation context for LLM.

    Args:
        session: CSM session data

    Returns:
        Formatted context string
    """
    parts = []

    parts.append(f"[원본 문의]\n{session['original_query'][:500]}")
    parts.append(f"[초기 답변]\n{session['initial_response'][:500]}")

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
        session = _csm_sessions.get(message_ts) if is_csm_response_channel else None

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

        # Create and save LearningEntry if we have learning content
        has_learning_content = any([
            learning_points_dict.get("query_lesson"),
            learning_points_dict.get("search_lesson"),
            learning_points_dict.get("response_lesson"),
        ])

        if has_learning_content:
            # Get responses from messages for learning entry
            bot_responses = [m.get("text", "") for m in messages if m.get("bot_id")]
            user_messages = [m.get("text", "") for m in messages if not m.get("bot_id")]

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

        # Update state (no confirmation message sent to keep thread clean)
        if entry_id:
            await set_message_state(channel, message_ts, MessageState.COMPLETED)
            logger.info(f"Archived thread to history: {entry_id}, learning: {learning_entry_id}")
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
