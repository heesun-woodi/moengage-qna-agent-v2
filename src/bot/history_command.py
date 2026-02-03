"""History RAG query commands for CSM channel."""

from typing import Dict, Any, Optional

from slack_sdk.web.async_client import AsyncWebClient

from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.utils.logger import logger


async def handle_history_command(
    client: AsyncWebClient,
    event: Dict[str, Any],
    user_query: str
):
    """Handle /history command for viewing History RAG entries.

    Args:
        client: Slack client
        event: The Slack event
        user_query: The user's query (including /history prefix)
    """
    channel = event.get("channel", "")
    message_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts", message_ts)

    # Parse command arguments
    args = user_query.replace('/history', '').strip()

    try:
        rag = get_history_rag()

        if args == 'stats':
            # Show statistics
            text = format_stats(rag.entries)
        elif args:
            # View specific entry by ID
            entry = find_entry(rag, args)
            if entry:
                text = format_entry_detail(entry)
            else:
                text = f"문서를 찾을 수 없습니다: `{args}`\n\n`/history`로 전체 목록을 확인하세요."
        else:
            # List all entries
            text = format_entry_list(rag.entries)

        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=text
        )

    except Exception as e:
        logger.error(f"Error in history command: {e}", exc_info=True)
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"오류가 발생했습니다: {str(e)[:100]}"
        )


def find_entry(rag, entry_id: str) -> Optional[HistoryEntry]:
    """Find entry by full ID or short ID prefix.

    Args:
        rag: History RAG instance
        entry_id: Full or partial entry ID

    Returns:
        HistoryEntry if found, None otherwise
    """
    # Try exact match first
    entry = rag.get_entry(entry_id)
    if entry:
        return entry

    # Try prefix match (short ID)
    entry_id_lower = entry_id.lower()
    for eid, entry in rag.entries.items():
        if eid.lower().startswith(entry_id_lower):
            return entry

    return None


def format_stats(entries: dict) -> str:
    """Format statistics message.

    Args:
        entries: Dictionary of entry_id -> HistoryEntry

    Returns:
        Formatted stats string
    """
    count = len(entries)

    # Count by category
    categories = {}
    customers = {}
    for entry in entries.values():
        cat = entry.category or "기타"
        categories[cat] = categories.get(cat, 0) + 1

        cust = entry.customer or "Unknown"
        customers[cust] = customers.get(cust, 0) + 1

    text = f"**History RAG 통계**\n\n"
    text += f"총 문서: **{count}개**\n\n"

    if categories:
        text += "**카테고리별:**\n"
        for cat, cnt in sorted(categories.items(), key=lambda x: -x[1]):
            text += f"- {cat}: {cnt}개\n"

    if customers:
        text += "\n**고객사별:**\n"
        for cust, cnt in sorted(customers.items(), key=lambda x: -x[1]):
            text += f"- {cust}: {cnt}개\n"

    return text


def format_entry_list(entries: dict) -> str:
    """Format entries list message.

    Args:
        entries: Dictionary of entry_id -> HistoryEntry

    Returns:
        Formatted list string
    """
    if not entries:
        return "저장된 문서가 없습니다."

    # Sort by created_at descending
    sorted_entries = sorted(
        entries.items(),
        key=lambda x: x[1].created_at if x[1].created_at else "",
        reverse=True
    )

    count = len(sorted_entries)
    text = f"**History RAG 문서 목록** (총 {count}개)\n\n"

    # Show max 15 entries
    for idx, (entry_id, entry) in enumerate(sorted_entries[:15], 1):
        short_id = entry_id[:8]
        title = entry.title[:50] + "..." if len(entry.title) > 50 else entry.title
        customer = entry.customer or "Unknown"
        category = entry.category or "기타"

        # Extract date from created_at
        date = entry.created_at[:10] if entry.created_at else "N/A"

        text += f"{idx}. `{short_id}` {title}\n"
        text += f"   {customer} | {category} | {date}\n\n"

    if count > 15:
        text += f"_... 외 {count - 15}개 문서_\n\n"

    text += "상세 조회: `/history <ID>`"

    return text


def format_entry_detail(entry: HistoryEntry) -> str:
    """Format single entry detail message.

    Args:
        entry: HistoryEntry to format

    Returns:
        Formatted detail string
    """
    text = "**문서 상세**\n\n"

    text += f"**제목**: {entry.title}\n"
    text += f"**ID**: `{entry.id}`\n"
    text += f"**고객**: {entry.customer or 'Unknown'}\n"
    text += f"**카테고리**: {entry.category or '기타'}\n"
    text += f"**생성일**: {entry.created_at[:10] if entry.created_at else 'N/A'}\n"

    if entry.channel_name:
        text += f"**채널**: {entry.channel_name}\n"

    text += "\n---\n\n"

    text += f"**문의 요약**:\n{entry.query_summary[:500]}\n"

    if len(entry.query_summary) > 500:
        text += "...\n"

    text += "\n---\n\n"

    text += f"**해결 내용**:\n{entry.solution[:1000]}\n"

    if len(entry.solution) > 1000:
        text += "...\n"

    if entry.url:
        text += f"\n**슬랙 스레드**: <{entry.url}|스레드 열기>\n"

    if entry.referenced_docs:
        text += f"\n**참고 문서**: {len(entry.referenced_docs)}개\n"

    return text
