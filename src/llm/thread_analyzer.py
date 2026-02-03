"""Thread analyzer for Slack support conversations."""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from src.llm.claude_client import get_claude_client
from src.utils.logger import logger


@dataclass
class ThreadAnalysis:
    """Result of thread analysis."""
    title: str
    category: str
    query_summary: str
    cause: str
    solution: str
    is_resolved: bool
    confidence: float
    raw_response: Dict[str, Any]


async def analyze_slack_thread(
    messages: List[Dict[str, Any]],
    customer: str = "진에어"
) -> ThreadAnalysis:
    """Analyze a Slack thread and extract structured information.

    Args:
        messages: List of Slack message dictionaries
        customer: Customer name for context

    Returns:
        ThreadAnalysis object
    """
    # Convert Slack messages to analysis format
    formatted_messages = []

    for msg in messages:
        # Determine role
        is_bot = msg.get("bot_id") is not None
        user_id = msg.get("user", "")

        if is_bot:
            role = "bot"
        elif formatted_messages:  # After first message
            role = "support"
        else:
            role = "customer"

        formatted_messages.append({
            "role": role,
            "text": msg.get("text", ""),
            "user": user_id,
            "timestamp": msg.get("ts", "")
        })

    # Analyze with Claude
    client = get_claude_client()
    result = await client.analyze_thread(formatted_messages)

    # Create ThreadAnalysis object
    analysis = ThreadAnalysis(
        title=result.get("title", "제목 없음"),
        category=result.get("category", "기타"),
        query_summary=result.get("query_summary", ""),
        cause=result.get("cause", ""),
        solution=result.get("solution", ""),
        is_resolved=result.get("is_resolved", False),
        confidence=result.get("confidence", 0.5),
        raw_response=result
    )

    logger.info(
        f"Thread analysis: '{analysis.title}' "
        f"(resolved: {analysis.is_resolved}, confidence: {analysis.confidence})"
    )

    return analysis


def detect_resolution_keywords(messages: List[Dict[str, Any]]) -> bool:
    """Quick check for resolution keywords in messages.

    Args:
        messages: List of messages

    Returns:
        True if resolution keywords found
    """
    resolution_keywords = [
        "해결", "감사합니다", "완료", "됐어요", "됐습니다",
        "worked", "solved", "thanks", "thank you", "perfect"
    ]

    for msg in messages[-3:]:  # Check last 3 messages
        text = msg.get("text", "").lower()
        if any(kw in text for kw in resolution_keywords):
            return True

    return False


def extract_email_content(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Extract forwarded email content from messages.

    Args:
        messages: List of messages

    Returns:
        Email content if found, None otherwise
    """
    email_indicators = [
        "from:", "to:", "subject:",
        "보낸 사람:", "받는 사람:", "제목:",
        "forwarded message", "전달된 메시지"
    ]

    for msg in messages:
        text = msg.get("text", "").lower()
        if any(indicator in text for indicator in email_indicators):
            return msg.get("text", "")

        # Check attachments for email forwards
        for attachment in msg.get("attachments", []):
            if attachment.get("pretext", "").lower().startswith("forwarded"):
                return attachment.get("text", "")

    return None


def count_participants(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count unique participants in a thread.

    Args:
        messages: List of messages

    Returns:
        Dictionary with participant types and counts
    """
    users = set()
    bots = set()

    for msg in messages:
        if msg.get("bot_id"):
            bots.add(msg.get("bot_id"))
        elif msg.get("user"):
            users.add(msg.get("user"))

    return {
        "total_users": len(users),
        "total_bots": len(bots),
        "total_messages": len(messages)
    }
