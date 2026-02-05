"""Response formatters for Slack messages."""

from typing import List, Optional

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
        f"_오류 내용: {error_message[:100]}_"
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
        f"✅ **문의 내용이 지원 히스토리에 저장되었습니다.**\n\n"
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
        "_✅ 최종 답변이 완성되면 원본 메시지에 :white_check_mark: 이모지를 추가해주세요._"
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
        f"✅ **학습 완료**\n\n"
        f"이 케이스가 학습 DB에 저장되었습니다. (ID: `{entry_id[:8]}...`)\n\n"
        f"**학습 포인트**\n{lessons_text}\n\n"
        "_이 경험은 향후 유사한 문의에 더 나은 답변을 생성하는 데 활용됩니다._"
    )
