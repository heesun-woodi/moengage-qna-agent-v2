"""Response formatters for Slack messages."""

import json
import re
from typing import List, Optional, Tuple

from src.knowledge.hybrid_searcher import UnifiedSearchResult


def format_support_response(
    response: str,
    search_results: Optional[List[UnifiedSearchResult]] = None,
    was_modified: bool = False,
    current_channel_id: str = ""
) -> str:
    """Format a support response for Slack.

    Args:
        response: The generated response text
        search_results: Search results used for the response
        was_modified: Whether the response was modified by grounding validation
        current_channel_id: Current Slack channel ID for filtering history URLs.
                           Pass None to show all URLs (for CSM channels).

    Returns:
        Formatted Slack message
    """
    # The response from Claude should already be formatted
    # Add source links if not already included
    if search_results and "참고 자료" not in response and "참고 문서" not in response:
        history_sources = []
        moengage_sources = []

        for result in search_results[:5]:  # Max 5 sources
            if result.source == "support_history":
                # Show URL if:
                # 1. current_channel_id is None (CSM channel - full access)
                # 2. no channel_id stored (backward compatibility)
                # 3. same channel
                result_channel_id = result.metadata.get("channel_id", "")
                if current_channel_id is None or not result_channel_id or result_channel_id == current_channel_id:
                    history_sources.append(f"- {result.title}: <{result.url}|슬랙 스레드>")
                else:
                    history_sources.append(f"- {result.title}")
            else:
                moengage_sources.append(f"- <{result.url}|{result.title}>")

        source_sections = []
        if history_sources:
            source_sections.append("[이전 Q&A]\n" + "\n".join(history_sources))
        if moengage_sources:
            source_sections.append("[MoEngage HelpCenter]\n" + "\n".join(moengage_sources))

        if source_sections:
            response += "\n\n**🔗 참고 자료**\n\n" + "\n\n".join(source_sections)

    return response


def format_error_response(error_message: str) -> str:
    """Format an error response for Slack.

    Args:
        error_message: The error message

    Returns:
        Formatted error message
    """
    return (
        "⚠️ **일시적인 오류가 발생했습니다.**\n\n"
        "잠시 후 다시 티켓 이모지(🎫)를 추가해 주세요.\n"
        "문제가 지속되면 MoEngage 대시보드를 통해 서포트 티켓을 생성해 주세요.\n\n"
        f"_오류 내용: {error_message[:300]}_"
    )


def format_archiving_confirmation(entry_id: str, title: str) -> str:
    """Format archiving confirmation message.

    Args:
        entry_id: The history entry ID
        title: The case title

    Returns:
        Formatted confirmation message
    """
    return (
        f":완료: **문의 내용이 지원 히스토리에 저장되었습니다.**\n\n"
        f"- 제목: {title}\n"
        f"- ID: `{entry_id[:8]}...`\n\n"
        "_이 기록은 향후 유사한 문의에 활용됩니다._"
    )


def format_no_results_response(query: str) -> str:
    """Format response when no search results found.

    Args:
        query: The original query

    Returns:
        Formatted message
    """
    return (
        "**🔍 문제 파악**\n"
        f"{query[:200]}...\n\n"
        "**❌ 검색 결과**\n"
        "문서에서 관련 내용을 찾을 수 없습니다.\n\n"
        "**💡 권장 조치**\n"
        "- 마켓핏랩 컨설턴트에게 문의해 주세요.\n"
        "- 또는 MoEngage 대시보드 → Support에서 티켓을 생성해 주세요."
    )


def truncate_text(text: str, max_length: int = 3000) -> str:
    """Truncate text to fit Slack message limits.

    Args:
        text: Text to truncate
        max_length: Maximum length

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    # Find a good breaking point
    truncated = text[:max_length - 100]

    # Try to break at paragraph
    last_para = truncated.rfind("\n\n")
    if last_para > max_length - 500:
        truncated = truncated[:last_para]

    return truncated + "\n\n_... (내용이 너무 길어 일부가 생략되었습니다)_"


def format_csm_ticket_response(
    response: str,
    original_query: str,
    original_channel: str,
    original_ts: str,
    search_results: Optional[List[UnifiedSearchResult]] = None,
    was_modified: bool = False,
    channel_name: str = ""
) -> str:
    """Format a ticket response for CSM channel with original message link.

    Args:
        response: The generated response text
        original_query: The original customer query
        original_channel: Channel ID where ticket was created
        original_ts: Timestamp of original message
        search_results: Search results used for the response
        was_modified: Whether the response was modified by grounding validation
        channel_name: Name of the original channel

    Returns:
        Formatted Slack message for CSM channel
    """
    # Generate message permalink
    message_link = f"https://slack.com/archives/{original_channel}/p{original_ts.replace('.', '')}"

    # Build header with channel name and original query context
    channel_display = f"**#{channel_name}**" if channel_name else ""
    header = (
        f"📋 **새로운 문의** {channel_display}\n\n"
        f"**원본 메시지**: <{message_link}|슬랙에서 보기>\n\n"
        f"**고객 문의 내용**:\n>{original_query[:500]}{'...' if len(original_query) > 500 else ''}\n\n"
        f"---\n\n"
    )

    # Add the response with sources
    formatted_response = format_support_response(
        response,
        search_results,
        was_modified,
        current_channel_id=None  # CSM gets full access to all URLs
    )

    # Add footer with instructions
    footer = (
        "\n\n---\n"
        "_💡 답변이 불충분하면 이 스레드에서 추가 질문을 해주세요._\n"
        "_:완료: 최종 답변이 완성되면 원본 메시지에 :완료: 이모지를 추가해주세요._"
    )

    return header + formatted_response + footer


def format_improved_response(
    response: str,
    iteration: int,
    search_results: Optional[List[UnifiedSearchResult]] = None
) -> str:
    """Format an improved response after CSM feedback.

    Args:
        response: The improved response text
        iteration: Iteration number (1, 2, 3, ...)
        search_results: Search results used for the response

    Returns:
        Formatted improved response
    """
    header = f"📝 **개선된 답변 (#{iteration})**\n\n"

    # Add sources if available
    formatted_response = format_support_response(
        response,
        search_results,
        was_modified=False,
        current_channel_id=None
    )

    return header + formatted_response


def format_learning_saved_confirmation(
    entry_id: str,
    learning_points: dict
) -> str:
    """Format confirmation message when learning is saved.

    Args:
        entry_id: The learning entry ID
        learning_points: Dictionary with query_lesson, search_lesson, response_lesson

    Returns:
        Formatted confirmation message
    """
    lessons = []
    if learning_points.get("query_lesson"):
        lessons.append(f"• 문의 해석: {learning_points['query_lesson'][:100]}")
    if learning_points.get("search_lesson"):
        lessons.append(f"• 검색 전략: {learning_points['search_lesson'][:100]}")
    if learning_points.get("response_lesson"):
        lessons.append(f"• 답변 작성: {learning_points['response_lesson'][:100]}")

    lessons_text = "\n".join(lessons) if lessons else "_학습 포인트 없음_"

    return (
        f":완료: **학습 완료**\n\n"
        f"이 케이스가 학습 DB에 저장되었습니다. (ID: `{entry_id[:8]}...`)\n\n"
        f"**학습 포인트**\n{lessons_text}\n\n"
        "_이 경험은 향후 유사한 문의에 더 나은 답변을 생성하는 데 활용됩니다._"
    )


# =============================================================================
# Block Kit formatters for CSM responses with delivery button
# =============================================================================

def _convert_to_slack_mrkdwn(text: str) -> str:
    """Convert standard markdown bold to Slack mrkdwn format.

    Slack Block Kit uses *bold* instead of **bold**.
    """
    return re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)


def _split_text_for_blocks(text: str, max_length: int = 2800) -> List[str]:
    """Split text into chunks that fit within Block Kit section limit (3000 chars).

    Splits at paragraph boundaries, then line boundaries, then hard-cuts.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split at paragraph boundary
        cut_point = remaining[:max_length].rfind("\n\n")
        if cut_point > max_length // 2:
            chunks.append(remaining[:cut_point])
            remaining = remaining[cut_point + 2:]
            continue

        # Try to split at line boundary
        cut_point = remaining[:max_length].rfind("\n")
        if cut_point > max_length // 2:
            chunks.append(remaining[:cut_point])
            remaining = remaining[cut_point + 1:]
            continue

        # Hard cut
        chunks.append(remaining[:max_length])
        remaining = remaining[max_length:]

    return chunks


def _build_source_text(
    search_results: Optional[List[UnifiedSearchResult]] = None
) -> str:
    """Build source links text from search results for Block Kit."""
    if not search_results:
        return ""

    history_sources = []
    moengage_sources = []

    for result in search_results[:5]:
        if result.source == "support_history":
            if result.url:
                history_sources.append(f"- {result.title}: <{result.url}|슬랙 스레드>")
            else:
                history_sources.append(f"- {result.title}")
        else:
            moengage_sources.append(f"- <{result.url}|{result.title}>")

    sections = []
    if history_sources:
        sections.append("[이전 Q&A]\n" + "\n".join(history_sources))
    if moengage_sources:
        sections.append("[MoEngage HelpCenter]\n" + "\n".join(moengage_sources))

    if sections:
        return "*🔗 참고 자료*\n\n" + "\n\n".join(sections)
    return ""


def _build_deliver_button_block(button_value: str = "") -> List[dict]:
    """Build the delivery prompt and button blocks."""
    button = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": "📨 고객에게 전달",
            "emoji": True
        },
        "style": "primary",
        "action_id": "deliver_to_customer",
    }
    # Slack requires value to be non-empty if present
    if button_value:
        button["value"] = button_value

    return [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "이 답변이 충분하시면 아래 버튼을 눌러 고객에게 전달해주세요.\n답변을 개선하려면 이 스레드에 @moengage qna 를 태그해서 피드백을 남겨주세요."
            }
        },
        {
            "type": "actions",
            "elements": [button]
        }
    ]


def build_csm_ticket_blocks(
    response: str,
    original_query: str,
    original_channel: str,
    original_ts: str,
    search_results: Optional[List[UnifiedSearchResult]] = None,
    was_modified: bool = False,
    channel_name: str = "",
    button_value: str = "",
    ticket_user: str = ""
) -> Tuple[List[dict], str]:
    """Build Block Kit blocks for initial CSM ticket response with delivery button.

    Returns:
        Tuple of (blocks, fallback_text)
    """
    message_link = f"https://slack.com/archives/{original_channel}/p{original_ts.replace('.', '')}"
    channel_display = f"*#{channel_name}*" if channel_name else ""

    blocks: List[dict] = []

    # Ticket user notification
    if ticket_user:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<@{ticket_user}> 님 답변이 작성되었습니다"}
        })

    # Header
    query_preview = original_query[:500] + ('...' if len(original_query) > 500 else '')
    header_text = (
        f"📋 *새로운 문의* {channel_display}\n\n"
        f"*원본 메시지*: <{message_link}|슬랙에서 보기>\n\n"
        f"*고객 문의 내용*:\n>{query_preview}"
    )

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": _convert_to_slack_mrkdwn(header_text)}
    })
    blocks.append({"type": "divider"})

    # Response body (split into chunks)
    response_with_sources = format_support_response(
        response, search_results, was_modified, current_channel_id=None
    )
    response_mrkdwn = _convert_to_slack_mrkdwn(response_with_sources)

    for chunk in _split_text_for_blocks(response_mrkdwn):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk}
        })

    # Delivery button
    blocks.extend(_build_deliver_button_block(button_value))

    # Fallback text
    fallback = format_csm_ticket_response(
        response, original_query, original_channel, original_ts,
        search_results, was_modified, channel_name
    )

    return blocks, fallback


def build_improved_response_blocks(
    response: str,
    iteration: int,
    search_results: Optional[List[UnifiedSearchResult]] = None,
    button_value: str = "",
    ticket_user: str = ""
) -> Tuple[List[dict], str]:
    """Build Block Kit blocks for an improved response with delivery button.

    Returns:
        Tuple of (blocks, fallback_text)
    """
    if ticket_user:
        header_text = f"<@{ticket_user}> 📝 *개선된 답변 (#{iteration})*"
    else:
        header_text = f"📝 *개선된 답변 (#{iteration})*"

    blocks: List[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text}
        }
    ]

    # Response body (split into chunks)
    response_with_sources = format_support_response(
        response, search_results, was_modified=False, current_channel_id=None
    )
    response_mrkdwn = _convert_to_slack_mrkdwn(response_with_sources)

    for chunk in _split_text_for_blocks(response_mrkdwn):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk}
        })

    # Delivery button
    blocks.extend(_build_deliver_button_block(button_value))

    # Fallback text
    fallback = format_improved_response(response, iteration, search_results)

    return blocks, fallback


def build_delivered_confirmation_blocks(
    original_blocks: List[dict],
    customer_thread_url: str
) -> List[dict]:
    """Replace delivery button in blocks with a delivery confirmation.

    Removes the actions block and delivery prompt, adds confirmation.
    """
    confirmed_blocks = []

    for block in original_blocks:
        # Skip the actions block and the delivery prompt context block
        if block.get("type") == "actions":
            continue
        if block.get("type") == "section":
            text_obj = block.get("text", {})
            if "고객에게 전달" in text_obj.get("text", "") and "태그해서" in text_obj.get("text", ""):
                continue
        confirmed_blocks.append(block)

    # Remove trailing divider if present (from the button section)
    if confirmed_blocks and confirmed_blocks[-1].get("type") == "divider":
        confirmed_blocks.pop()

    # Add confirmation
    confirmed_blocks.append({"type": "divider"})
    confirmed_blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":완료: *전달 완료* | <{customer_thread_url}|고객 스레드에서 보기>"
        }
    })

    return confirmed_blocks


def format_customer_response(
    response: str,
    search_results: Optional[List[UnifiedSearchResult]] = None,
    current_channel_id: Optional[str] = None
) -> str:
    """Format response for delivery to customer thread.

    Strips CSM-specific headers (e.g. 문제 파악 section), adds greeting,
    and returns answer + source links.
    """
    formatted = format_support_response(
        response,
        search_results,
        was_modified=False,
        current_channel_id=current_channel_id or ""
    )

    # Remove "문제 파악" section (from start of section header to next ** header)
    formatted = re.sub(
        r'\*\*🔍\s*문제 파악\*\*\s*\n.*?(?=\*\*[^\*])',
        '',
        formatted,
        flags=re.DOTALL
    )
    formatted = formatted.strip()

    # Add greeting at the top
    greeting = (
        "안녕하세요. 문의하신 사항에 대해 답변드립니다.\n"
        "답변내용을 확인하시고 문의사항이 해소가 되지 않으신다면 추가 문의 해주시길 부탁드립니다.\n\n"
    )

    return greeting + formatted


def update_button_value(blocks: List[dict], new_value: str) -> List[dict]:
    """Update the value of the deliver_to_customer button in blocks."""
    for block in blocks:
        if block.get("type") == "actions":
            for element in block.get("elements", []):
                if element.get("action_id") == "deliver_to_customer":
                    element["value"] = new_value
    return blocks


# Source metadata: (emoji, label)
_SOURCE_META = {
    "moengage_docs":   ("📚", "MoEngage User Guide"),
    "developer_docs":  ("⚙️", "MoEngage Developer Guide"),
    "partner_docs":    ("🤝", "MoEngage Partner Guide"),
    "support_history": ("📋", "지원 히스토리"),
    "slack_thread":    ("💬", "Slack 스레드"),
    "notion":          ("📝", "Notion 문서"),
}

_SOURCE_ORDER = ["support_history", "moengage_docs", "developer_docs", "partner_docs", "slack_thread", "notion"]


def build_csm_resource_list_blocks(
    search_results: List[UnifiedSearchResult],
    original_query: str,
    original_channel: str,
    original_ts: str,
    channel_name: str = "",
    ticket_user: str = "",
    pre_rerank_by_source: Optional[dict] = None,
    post_rerank_by_source: Optional[dict] = None
) -> Tuple[List[dict], str]:
    """Build Block Kit blocks showing a list of related resources (no generated answer).

    This replaces build_csm_ticket_blocks() for the initial response.
    CSM can then request additional searches or ask the bot to write a response.

    Returns:
        Tuple of (blocks, fallback_text)
    """
    from src.knowledge.hybrid_searcher import group_results_by_source

    message_link = f"https://slack.com/archives/{original_channel}/p{original_ts.replace('.', '')}"
    channel_display = f"*#{channel_name}*" if channel_name else ""

    blocks: List[dict] = []

    # User notification
    if ticket_user:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<@{ticket_user}> 님 관련 자료를 찾았습니다"}
        })

    # Header
    query_preview = original_query[:400] + ("..." if len(original_query) > 400 else "")
    header_text = (
        f"📋 *새로운 문의* {channel_display}\n\n"
        f"*원본 메시지*: <{message_link}|슬랙에서 보기>\n\n"
        f"*고객 문의 내용*:\n>{query_preview}"
    )
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": header_text}
    })
    blocks.append({"type": "divider"})

    # Group results by source
    grouped = group_results_by_source(search_results)
    total_count = len(search_results)

    if total_count == 0:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_관련 자료를 찾지 못했습니다. 스레드에서 다른 키워드로 검색을 요청해보세요._"
            }
        })
    else:
        # Add each source section
        for source_key in _SOURCE_ORDER:
            items = grouped.get(source_key, [])
            if not items:
                continue

            emoji, label = _SOURCE_META.get(source_key, ("📄", source_key))
            section_header = f"*{emoji} {label}* ({len(items)}건)"

            item_lines = []
            for item in items[:5]:  # Max 5 per source
                title = item.title[:60] + ("..." if len(item.title) > 60 else "")
                # Only show snippet if it was set by reranker (Korean summary)
                # Skip raw English snippets from Zendesk API
                has_rerank_summary = item.metadata.get("evidence_type", "") if item.metadata else ""
                snippet = ""
                if has_rerank_summary:
                    raw_snippet = item.snippet[:120].replace("\n", " ").strip()
                    if raw_snippet:
                        snippet = f" — _{raw_snippet}_"
                url = item.url
                if url and not url.startswith("history://"):
                    item_lines.append(f"• <{url}|{title}>{snippet}")
                else:
                    item_lines.append(f"• {title}{snippet}")

            section_text = section_header + "\n" + "\n".join(item_lines)
            # Split if too long
            for chunk in _split_text_for_blocks(section_text, max_length=2800):
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": chunk}
                })

    # Source-level search status
    if pre_rerank_by_source:
        status_lines = []
        for source_key in _SOURCE_ORDER:
            if source_key == "support_history":
                continue
            emoji, label = _SOURCE_META.get(source_key, ("📄", source_key))
            pre_count = pre_rerank_by_source.get(source_key, 0)
            post_count = (post_rerank_by_source or {}).get(source_key, 0)

            if post_count > 0:
                # Already shown in resource list above — skip
                continue
            elif pre_count > 0:
                # Searched and found, but filtered by relevance
                status_lines.append(
                    f"{emoji} {label}: {pre_count}건 검색되었으나 관련성이 낮아 제외됨 "
                    f"(_\"관련성 낮은 자료도 보여줘\"로 요청 가능_)"
                )
            else:
                # No results at all
                status_lines.append(
                    f"{emoji} {label}: 문의와 관련된 자료를 찾지 못했습니다"
                )

        if status_lines:
            status_text = "\n".join(status_lines)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": status_text}
            })

    # Footer guide
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*💬 스레드에서 요청하세요:*\n"
                "• 다른 자료 더 찾기: \"[키워드] 관련 자료 찾아줘\"\n"
                "• 답변 작성: \"위 자료로 고객 답변 써줘\""
            )
        }
    })

    # Fallback plain text
    fallback_lines = [
        f"📋 새로운 문의 {channel_name and f'#{channel_name}' or ''}",
        f"원본 메시지: {message_link}",
        f"고객 문의: {query_preview}",
        "",
        f"관련 자료 {total_count}건 발견:",
    ]
    for result in search_results[:10]:
        emoji = _SOURCE_META.get(result.source, ("📄", ""))[0]
        fallback_lines.append(f"{emoji} {result.title}: {result.url}")
    fallback_text = "\n".join(fallback_lines)

    return blocks, fallback_text
