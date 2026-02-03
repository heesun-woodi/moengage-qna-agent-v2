"""Feedback storage for answer quality evaluation.

Stores user feedback on bot responses to improve search quality over time.
"""

import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from pathlib import Path

from src.utils.logger import logger


@dataclass
class FeedbackEntry:
    """Feedback entry for a bot response."""
    id: str
    channel: str
    message_ts: str
    original_query: str
    optimized_query: str
    search_results_count: int
    moengage_results_count: int
    history_results_count: int
    response_length: int
    feedback: str  # "positive", "negative", "none"
    feedback_user: Optional[str] = None
    feedback_ts: Optional[str] = None
    created_at: str = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        if self.metadata is None:
            self.metadata = {}


class FeedbackStore:
    """JSON-based feedback storage."""

    def __init__(self, store_path: str = "./data/feedback"):
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.feedback_file = self.store_path / "feedback.json"
        self._load_store()

    def _load_store(self):
        """Load feedback entries from file."""
        self.entries: Dict[str, FeedbackEntry] = {}

        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for entry_data in data.get("entries", []):
                        entry = FeedbackEntry(**entry_data)
                        self.entries[entry.id] = entry
                logger.info(f"Loaded {len(self.entries)} feedback entries")
            except Exception as e:
                logger.error(f"Failed to load feedback store: {e}")
                self.entries = {}

    def _save_store(self):
        """Save feedback entries to file."""
        try:
            data = {
                "entries": [asdict(e) for e in self.entries.values()],
                "updated_at": datetime.now().isoformat()
            }
            with open(self.feedback_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save feedback store: {e}")

    def create_entry(
        self,
        channel: str,
        message_ts: str,
        original_query: str,
        optimized_query: str,
        search_results_count: int,
        moengage_results_count: int,
        history_results_count: int,
        response_length: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create a new feedback entry (without feedback yet).

        Args:
            channel: Slack channel ID
            message_ts: Message timestamp (used as unique ID)
            original_query: Original user query
            optimized_query: LLM-optimized search query
            search_results_count: Total search results
            moengage_results_count: MoEngage doc results
            history_results_count: History RAG results
            response_length: Length of bot response
            metadata: Additional metadata

        Returns:
            Entry ID (message_ts)
        """
        entry_id = f"{channel}_{message_ts}"

        entry = FeedbackEntry(
            id=entry_id,
            channel=channel,
            message_ts=message_ts,
            original_query=original_query,
            optimized_query=optimized_query,
            search_results_count=search_results_count,
            moengage_results_count=moengage_results_count,
            history_results_count=history_results_count,
            response_length=response_length,
            feedback="none",
            metadata=metadata or {}
        )

        self.entries[entry_id] = entry
        self._save_store()

        logger.info(f"Created feedback entry: {entry_id}")
        return entry_id

    def add_feedback(
        self,
        channel: str,
        message_ts: str,
        feedback: str,
        user: str
    ) -> bool:
        """Add user feedback to an existing entry.

        Args:
            channel: Slack channel ID
            message_ts: Message timestamp
            feedback: "positive" or "negative"
            user: User who provided feedback

        Returns:
            True if feedback was added successfully
        """
        entry_id = f"{channel}_{message_ts}"

        if entry_id not in self.entries:
            logger.warning(f"Feedback entry not found: {entry_id}")
            return False

        entry = self.entries[entry_id]
        entry.feedback = feedback
        entry.feedback_user = user
        entry.feedback_ts = datetime.now().isoformat()

        self._save_store()

        logger.info(f"Added {feedback} feedback to entry: {entry_id}")
        return True

    def get_entry(self, channel: str, message_ts: str) -> Optional[FeedbackEntry]:
        """Get a feedback entry by channel and message timestamp."""
        entry_id = f"{channel}_{message_ts}"
        return self.entries.get(entry_id)

    def get_statistics(self) -> Dict[str, Any]:
        """Get feedback statistics.

        Returns:
            Dictionary with feedback statistics
        """
        total = len(self.entries)
        positive = sum(1 for e in self.entries.values() if e.feedback == "positive")
        negative = sum(1 for e in self.entries.values() if e.feedback == "negative")
        no_feedback = sum(1 for e in self.entries.values() if e.feedback == "none")

        # Calculate averages for positive vs negative
        positive_entries = [e for e in self.entries.values() if e.feedback == "positive"]
        negative_entries = [e for e in self.entries.values() if e.feedback == "negative"]

        avg_results_positive = (
            sum(e.search_results_count for e in positive_entries) / len(positive_entries)
            if positive_entries else 0
        )
        avg_results_negative = (
            sum(e.search_results_count for e in negative_entries) / len(negative_entries)
            if negative_entries else 0
        )

        return {
            "total_entries": total,
            "positive_feedback": positive,
            "negative_feedback": negative,
            "no_feedback": no_feedback,
            "positive_rate": positive / (positive + negative) if (positive + negative) > 0 else 0,
            "avg_search_results_positive": avg_results_positive,
            "avg_search_results_negative": avg_results_negative,
        }

    def get_negative_feedback_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get queries that received negative feedback for analysis.

        Args:
            limit: Maximum number of queries to return

        Returns:
            List of query data for improvement analysis
        """
        negative = [
            {
                "original_query": e.original_query,
                "optimized_query": e.optimized_query,
                "search_results_count": e.search_results_count,
                "created_at": e.created_at
            }
            for e in self.entries.values()
            if e.feedback == "negative"
        ]

        # Sort by most recent
        negative.sort(key=lambda x: x["created_at"], reverse=True)

        return negative[:limit]


# Global instance
_feedback_store: Optional[FeedbackStore] = None


def get_feedback_store() -> FeedbackStore:
    """Get or create the global feedback store instance."""
    global _feedback_store
    if _feedback_store is None:
        _feedback_store = FeedbackStore()
    return _feedback_store
