"""History updater - Automatically add resolved cases to History RAG."""

from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

from src.knowledge.history_rag import get_history_rag, HistoryEntry
from src.utils.logger import logger


@dataclass
class ThreadMessage:
    """A message from a Slack thread."""
    user_id: str
    user_name: str
    text: str
    timestamp: str
    is_bot: bool = False
    attachments: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


@dataclass
class ResolvedCase:
    """A resolved support case to be added to history."""
    title: str
    customer: str
    category: str
    query_summary: str
    solution: str
    thread_url: str
    resolved_at: str
    messages: List[ThreadMessage]
    channel_id: str = ""
    channel_name: str = ""
    referenced_docs: List[str] = None
    referenced_history: List[str] = None
    metadata: Dict[str, Any] = None
    source_type: str = "customer"  # "customer" | "csm"

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.referenced_docs is None:
            self.referenced_docs = []
        if self.referenced_history is None:
            self.referenced_history = []


@dataclass
class QueryInterpretation:
    """Query interpretation learning data."""
    initial: str = ""  # Bot's initial interpretation
    corrections: List[str] = field(default_factory=list)  # CSM corrections
    final: str = ""  # Final correct interpretation


@dataclass
class SearchIteration:
    """A single search iteration."""
    query: str = ""
    results: List[str] = field(default_factory=list)


@dataclass
class SearchHistory:
    """Search learning data."""
    initial_queries: List[str] = field(default_factory=list)
    initial_results: List[str] = field(default_factory=list)
    additional_searches: List[SearchIteration] = field(default_factory=list)
    used_documents: List[str] = field(default_factory=list)  # Actually used in final answer


@dataclass
class ResponseEvolution:
    """Response evolution learning data."""
    initial_response: str = ""
    feedback: List[str] = field(default_factory=list)  # CSM feedback messages
    iterations: List[str] = field(default_factory=list)  # Improved responses
    final_response: str = ""


@dataclass
class LearningPoints:
    """Extracted learning points from the conversation."""
    query_lesson: str = ""  # What we learned about query interpretation
    search_lesson: str = ""  # What we learned about searching
    response_lesson: str = ""  # What we learned about response generation


@dataclass
class LearningEntry:
    """Complete learning entry from a CSM-improved conversation.

    Captures the full learning cycle:
    1. Query understanding improvements
    2. Search strategy improvements
    3. Response generation improvements
    """
    id: str = ""
    original_query: str = ""
    original_channel: str = ""
    original_ts: str = ""
    csm_thread_channel: str = ""
    csm_thread_ts: str = ""

    # Learning components
    query_interpretation: QueryInterpretation = field(default_factory=QueryInterpretation)
    search_history: SearchHistory = field(default_factory=SearchHistory)
    response_evolution: ResponseEvolution = field(default_factory=ResponseEvolution)
    learning_points: LearningPoints = field(default_factory=LearningPoints)

    # Metadata
    customer: str = ""
    category: str = ""
    created_at: str = ""
    completed_at: str = ""
    csm_user_id: str = ""
    csm_user_name: str = ""
    iteration_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "original_query": self.original_query,
            "original_channel": self.original_channel,
            "original_ts": self.original_ts,
            "csm_thread_channel": self.csm_thread_channel,
            "csm_thread_ts": self.csm_thread_ts,
            "query_interpretation": {
                "initial": self.query_interpretation.initial,
                "corrections": self.query_interpretation.corrections,
                "final": self.query_interpretation.final,
            },
            "search_history": {
                "initial_queries": self.search_history.initial_queries,
                "initial_results": self.search_history.initial_results,
                "additional_searches": [
                    {"query": s.query, "results": s.results}
                    for s in self.search_history.additional_searches
                ],
                "used_documents": self.search_history.used_documents,
            },
            "response_evolution": {
                "initial_response": self.response_evolution.initial_response,
                "feedback": self.response_evolution.feedback,
                "iterations": self.response_evolution.iterations,
                "final_response": self.response_evolution.final_response,
            },
            "learning_points": {
                "query_lesson": self.learning_points.query_lesson,
                "search_lesson": self.learning_points.search_lesson,
                "response_lesson": self.learning_points.response_lesson,
            },
            "customer": self.customer,
            "category": self.category,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "csm_user_id": self.csm_user_id,
            "csm_user_name": self.csm_user_name,
            "iteration_count": self.iteration_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearningEntry":
        """Create from dictionary."""
        entry = cls(
            id=data.get("id", ""),
            original_query=data.get("original_query", ""),
            original_channel=data.get("original_channel", ""),
            original_ts=data.get("original_ts", ""),
            csm_thread_channel=data.get("csm_thread_channel", ""),
            csm_thread_ts=data.get("csm_thread_ts", ""),
            customer=data.get("customer", ""),
            category=data.get("category", ""),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at", ""),
            csm_user_id=data.get("csm_user_id", ""),
            csm_user_name=data.get("csm_user_name", ""),
            iteration_count=data.get("iteration_count", 0),
        )

        # Parse query interpretation
        qi = data.get("query_interpretation", {})
        entry.query_interpretation = QueryInterpretation(
            initial=qi.get("initial", ""),
            corrections=qi.get("corrections", []),
            final=qi.get("final", ""),
        )

        # Parse search history
        sh = data.get("search_history", {})
        entry.search_history = SearchHistory(
            initial_queries=sh.get("initial_queries", []),
            initial_results=sh.get("initial_results", []),
            additional_searches=[
                SearchIteration(query=s.get("query", ""), results=s.get("results", []))
                for s in sh.get("additional_searches", [])
            ],
            used_documents=sh.get("used_documents", []),
        )

        # Parse response evolution
        re = data.get("response_evolution", {})
        entry.response_evolution = ResponseEvolution(
            initial_response=re.get("initial_response", ""),
            feedback=re.get("feedback", []),
            iterations=re.get("iterations", []),
            final_response=re.get("final_response", ""),
        )

        # Parse learning points
        lp = data.get("learning_points", {})
        entry.learning_points = LearningPoints(
            query_lesson=lp.get("query_lesson", ""),
            search_lesson=lp.get("search_lesson", ""),
            response_lesson=lp.get("response_lesson", ""),
        )

        return entry


# Category mapping for classification
CATEGORY_KEYWORDS = {
    "채널 세팅 및 연동": ["카카오", "kakao", "sms", "이메일", "email", "채널", "channel", "연동"],
    "써드파티 연동": ["써드파티", "third-party", "외부", "연동", "integration", "connector"],
    "데이터 모델": ["데이터", "data", "속성", "attribute", "이벤트", "event", "모델"],
    "SDK 설치": ["sdk", "설치", "install", "초기화", "initialize", "앱"],
    "Analyze 분석 기능": ["분석", "analytics", "analyze", "리포트", "report", "대시보드", "dashboard"],
    "유저 세그먼테이션": ["세그먼트", "segment", "타겟", "target", "필터", "filter"],
    "캠페인 세팅": ["캠페인", "campaign", "푸시", "push", "인앱", "in-app", "발송"],
    "기본 UI 가이드": ["ui", "화면", "메뉴", "설정", "settings", "대시보드"]
}


def classify_category(text: str) -> str:
    """Classify case into one of the 8 categories.

    Args:
        text: Text to classify (usually query + solution)

    Returns:
        Category name
    """
    text_lower = text.lower()

    # Count keyword matches for each category
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        scores[category] = score

    # Return category with highest score, or default
    if max(scores.values()) > 0:
        return max(scores, key=scores.get)

    return "기타"


def extract_solution_from_thread(messages: List[ThreadMessage]) -> str:
    """Extract the solution from a thread of messages.

    Args:
        messages: List of thread messages

    Returns:
        Extracted solution text
    """
    solution_parts = []

    for msg in messages:
        # Skip very short messages
        if len(msg.text) < 20:
            continue

        # Bot messages are likely solutions
        if msg.is_bot:
            solution_parts.append(f"[Bot] {msg.text}")
        # Non-customer messages after the first one are likely from support
        elif msg != messages[0]:
            solution_parts.append(msg.text)

    if not solution_parts:
        # Fallback: use all messages after the first
        solution_parts = [m.text for m in messages[1:] if len(m.text) > 20]

    return "\n\n".join(solution_parts[:3])  # Limit to 3 parts


async def add_resolved_case(case: ResolvedCase) -> str:
    """Add a resolved case to the History RAG.

    Args:
        case: ResolvedCase to add

    Returns:
        Entry ID
    """
    rag = get_history_rag()

    # Auto-classify if category not provided
    category = case.category
    if not category or category == "기타":
        category = classify_category(f"{case.query_summary} {case.solution}")

    # Create history entry
    entry = HistoryEntry(
        id="",  # Will be auto-generated
        title=case.title,
        customer=case.customer,
        category=category,
        query_summary=case.query_summary,
        solution=case.solution,
        created_at=case.resolved_at,
        url=case.thread_url,
        channel_id=case.channel_id,
        channel_name=case.channel_name,
        referenced_docs=case.referenced_docs,
        referenced_history=case.referenced_history,
        metadata={
            "message_count": len(case.messages),
            **case.metadata
        },
        source_type=case.source_type
    )

    entry_id = rag.add_entry(entry)
    logger.info(f"Added resolved case to history: {case.title} (ID: {entry_id})")

    return entry_id


async def add_from_slack_thread(
    thread_messages: List[Dict[str, Any]],
    customer: str = "진에어",
    thread_url: str = "",
    channel_id: str = "",
    channel_name: str = "",
    referenced_docs: List[str] = None,
    referenced_history: List[str] = None,
    source_type: str = "customer",
    csm_metadata: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Add a resolved case from Slack thread data.

    Args:
        thread_messages: List of Slack message dictionaries
        customer: Customer name
        thread_url: URL to the thread
        channel_id: Slack channel ID
        channel_name: Slack channel name
        referenced_docs: List of MoEngage doc URLs referenced in the answer
        referenced_history: List of History RAG URLs referenced in the answer
        source_type: "customer" or "csm"
        csm_metadata: CSM user info (for CSM channel entries)

    Returns:
        Entry ID or None if failed
    """
    if referenced_docs is None:
        referenced_docs = []
    if referenced_history is None:
        referenced_history = []
    if csm_metadata is None:
        csm_metadata = {}
    if not thread_messages:
        logger.warning("No messages provided for history update")
        return None

    # Convert Slack messages to ThreadMessage
    messages = []
    for msg in thread_messages:
        messages.append(ThreadMessage(
            user_id=msg.get("user", ""),
            user_name=msg.get("username", msg.get("user", "")),
            text=msg.get("text", ""),
            timestamp=msg.get("ts", ""),
            is_bot=msg.get("bot_id") is not None,
            attachments=msg.get("attachments", [])
        ))

    # First message is the query
    first_msg = messages[0] if messages else None
    if not first_msg:
        return None

    # Extract query summary from first message
    query_summary = first_msg.text[:500]

    # Extract solution from subsequent messages
    solution = extract_solution_from_thread(messages)

    # Generate title from first message
    title = query_summary[:50]
    if len(query_summary) > 50:
        title += "..."

    # Create resolved case
    case = ResolvedCase(
        title=title,
        customer=customer,
        category="",  # Will be auto-classified
        query_summary=query_summary,
        solution=solution,
        thread_url=thread_url,
        resolved_at=datetime.now().isoformat(),
        messages=messages,
        channel_id=channel_id,
        channel_name=channel_name,
        referenced_docs=referenced_docs,
        referenced_history=referenced_history,
        metadata=csm_metadata,
        source_type=source_type
    )

    return await add_resolved_case(case)


def get_history_stats() -> Dict[str, Any]:
    """Get statistics about the history database.

    Returns:
        Dictionary with stats
    """
    rag = get_history_rag()

    return {
        "total_entries": rag.count(),
        "persist_dir": rag.persist_dir
    }
