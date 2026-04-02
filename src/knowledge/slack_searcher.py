"""Slack message searcher using search.messages API (requires User Token)."""

import asyncio
from typing import List, Optional

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from src.utils.logger import logger


class SlackSearcher:
    """Search Slack messages using user-level search API."""

    def __init__(self, user_token: str, channel_ids: Optional[List[str]] = None):
        """Initialize Slack searcher.

        Args:
            user_token: Slack User OAuth Token (xoxp-...) with search:read scope
            channel_ids: Optional list of channel IDs to restrict search to
        """
        self.client = AsyncWebClient(token=user_token)
        self.channel_ids = channel_ids or []
        self._channel_names: dict = {}  # Cache channel_id -> name

    async def _resolve_channel_names(self) -> None:
        """Resolve channel IDs to names for search query construction."""
        if self._channel_names:
            return
        for channel_id in self.channel_ids:
            try:
                info = await self.client.conversations_info(channel=channel_id)
                name = info.get("channel", {}).get("name", channel_id)
                self._channel_names[channel_id] = name
            except Exception:
                self._channel_names[channel_id] = channel_id

    def _build_search_query(self, query: str) -> str:
        """Build search query with optional channel filters.

        Slack search supports "in:<#CHANNEL_ID>" syntax for channel IDs.
        """
        if not self.channel_ids:
            return query

        channel_filters = []
        for channel_id in self.channel_ids:
            name = self._channel_names.get(channel_id)
            if name and name != channel_id:
                # Use resolved name (most reliable)
                channel_filters.append(f"in:{name}")
            else:
                # Use ID format with angle brackets (works without name resolution)
                channel_filters.append(f"in:<#{channel_id}>")

        channel_part = " OR ".join(channel_filters)
        return f"({channel_part}) {query}"

    async def search(self, query: str, top_k: int = 5) -> List[dict]:
        """Search Slack messages.

        Args:
            query: Search query string
            top_k: Maximum number of results to return

        Returns:
            List of UnifiedSearchResult-compatible dicts
        """
        from src.knowledge.hybrid_searcher import UnifiedSearchResult

        try:
            # Resolve channel names for filter construction
            if self.channel_ids and not self._channel_names:
                await self._resolve_channel_names()

            search_query = self._build_search_query(query)
            logger.info(f"[SLACK SEARCH] Query: {search_query[:100]}")

            response = await self.client.search_messages(
                query=search_query,
                count=top_k,
                sort="score",
                sort_dir="desc"
            )

            matches = response.get("messages", {}).get("matches", [])
            logger.info(f"[SLACK SEARCH] Found {len(matches)} matches")

            results = []
            for match in matches[:top_k]:
                channel_info = match.get("channel", {})
                channel_name = channel_info.get("name", "")
                channel_id = channel_info.get("id", "")
                text = match.get("text", "")
                ts = match.get("ts", "")
                permalink = match.get("permalink", "")

                # Get thread context if this is part of a thread
                thread_text = text
                thread_ts = match.get("thread_ts")
                if thread_ts and thread_ts != ts:
                    try:
                        thread_result = await self.client.conversations_replies(
                            channel=channel_id,
                            ts=thread_ts,
                            limit=10
                        )
                        thread_messages = thread_result.get("messages", [])
                        if thread_messages:
                            thread_text = "\n".join([
                                m.get("text", "") for m in thread_messages
                                if m.get("text")
                            ])
                            # Use thread permalink if available
                            if not permalink:
                                permalink = f"https://slack.com/archives/{channel_id}/p{thread_ts.replace('.', '')}"
                    except Exception as e:
                        logger.debug(f"Could not fetch thread context: {e}")

                # Create a meaningful title from the message
                title_text = text[:80].replace("\n", " ").strip()
                if len(text) > 80:
                    title_text += "..."
                title = f"[#{channel_name}] {title_text}" if channel_name else title_text

                snippet = text[:300].strip()

                results.append(UnifiedSearchResult(
                    title=title,
                    url=permalink or f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}",
                    content=thread_text[:2000],
                    snippet=snippet,
                    source="slack_thread",
                    score=0.8,
                    metadata={
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "ts": ts,
                        "thread_ts": thread_ts or ts,
                    }
                ))

            return results

        except SlackApiError as e:
            logger.error(f"[SLACK SEARCH] Slack API error: {e.response.get('error', str(e))}")
            return []
        except Exception as e:
            logger.error(f"[SLACK SEARCH] Search failed: {e}")
            return []


# Singleton instance
_slack_searcher: Optional[SlackSearcher] = None


def get_slack_searcher() -> Optional[SlackSearcher]:
    """Get or create Slack searcher singleton.

    Returns None if SLACK_USER_TOKEN is not configured.
    """
    global _slack_searcher
    if _slack_searcher is not None:
        return _slack_searcher

    from config.settings import settings
    if not settings.slack_user_token:
        return None

    _slack_searcher = SlackSearcher(
        user_token=settings.slack_user_token,
        channel_ids=settings.slack_search_channel_id_list
    )
    logger.info(
        f"[SLACK SEARCH] Initialized with "
        f"{len(settings.slack_search_channel_id_list)} channel filters"
    )
    return _slack_searcher
