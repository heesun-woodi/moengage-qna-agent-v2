"""Hybrid searcher combining MoEngage API and History RAG."""

import asyncio
from typing import List, Optional, Tuple
from dataclasses import dataclass

from src.knowledge.moengage_api import search_moengage, SearchResult
from src.knowledge.history_rag import search_history, HistorySearchResult
from src.llm.query_optimizer import analyze_query
from src.utils.logger import logger


@dataclass
class UnifiedSearchResult:
    """Unified search result from any source."""
    title: str
    url: str
    content: str
    snippet: str
    source: str
    score: float = 1.0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


async def hybrid_search(
    query: str,
    moengage_top_k: int = 5,
    history_top_k: int = 3,
    prioritize_history: bool = True,
    use_llm_optimization: bool = True
) -> List[UnifiedSearchResult]:
    """Perform hybrid search across MoEngage API and History RAG.

    Args:
        query: Search query
        moengage_top_k: Number of MoEngage results
        history_top_k: Number of History results
        prioritize_history: Whether to prioritize history results
        use_llm_optimization: Whether to use LLM to optimize search query

    Returns:
        List of unified search results
    """
    logger.info(f"Hybrid search for: '{query}'")

    # Optimize query using LLM if enabled
    optimized_query = query
    query_analysis = None

    if use_llm_optimization:
        try:
            query_analysis = await analyze_query(query)
            optimized_query = query_analysis.get("search_query", query)
            logger.info(f"LLM optimized query: '{optimized_query}'")
            logger.info(f"Detected intent: {query_analysis.get('intent', 'unknown')}")
            logger.info(f"Related features: {query_analysis.get('moengage_features', [])}")
        except Exception as e:
            logger.warning(f"Query optimization failed, using original: {e}")
            optimized_query = query

    # Run searches in parallel with optimized query for both sources
    moengage_task = search_moengage(optimized_query, top_k=moengage_top_k)
    history_task = search_history(optimized_query, top_k=history_top_k)  # Use optimized for consistency

    try:
        moengage_results, history_results = await asyncio.gather(
            moengage_task,
            history_task,
            return_exceptions=True
        )
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        moengage_results = []
        history_results = []

    # Handle exceptions from gather
    if isinstance(moengage_results, Exception):
        logger.error(f"MoEngage search failed: {moengage_results}")
        moengage_results = []
    else:
        logger.info(f"[HYBRID] MoEngage API returned {len(moengage_results)} results")

    if isinstance(history_results, Exception):
        logger.error(f"History search failed: {history_results}")
        history_results = []
    else:
        logger.info(f"[HYBRID] History RAG returned {len(history_results)} results")

    # Convert to unified results
    unified_results: List[UnifiedSearchResult] = []

    # Add history results first if prioritizing
    if prioritize_history:
        for hr in history_results:
            # Include both query and solution for better context
            content = f"문의: {hr.entry.query_summary}\n\n답변: {hr.entry.solution}"
            unified_results.append(UnifiedSearchResult(
                title=hr.entry.title,
                url=hr.entry.url or f"history://{hr.entry.id}",
                content=content,
                snippet=hr.entry.query_summary[:200],
                source="support_history",
                score=hr.score,
                metadata={
                    "customer": hr.entry.customer,
                    "category": hr.entry.category,
                    "created_at": hr.entry.created_at,
                    "channel_id": hr.entry.channel_id,
                    "channel_name": hr.entry.channel_name,
                    "entry_id": hr.entry.id
                }
            ))

    # Add MoEngage results with rank-based scoring
    for i, mr in enumerate(moengage_results):
        # Higher rank = higher score (0.9 for first, decreasing by 0.03)
        score = max(0.7, 0.9 - (i * 0.03))
        unified_results.append(UnifiedSearchResult(
            title=mr.title,
            url=mr.url,
            content=mr.content,
            snippet=mr.snippet,
            source="moengage_docs",
            score=score,
            metadata={"rank": i + 1}
        ))

    # Add history results last if not prioritizing
    if not prioritize_history:
        for hr in history_results:
            content = f"문의: {hr.entry.query_summary}\n\n답변: {hr.entry.solution}"
            unified_results.append(UnifiedSearchResult(
                title=hr.entry.title,
                url=hr.entry.url or f"history://{hr.entry.id}",
                content=content,
                snippet=hr.entry.query_summary[:200],
                source="support_history",
                score=hr.score,
                metadata={
                    "customer": hr.entry.customer,
                    "category": hr.entry.category,
                    "created_at": hr.entry.created_at,
                    "channel_id": hr.entry.channel_id,
                    "channel_name": hr.entry.channel_name,
                    "entry_id": hr.entry.id
                }
            ))

    # Deduplicate by URL
    unified_results = _deduplicate_results(unified_results)

    # Limit total results
    max_results = moengage_top_k + history_top_k
    unified_results = unified_results[:max_results]

    logger.info(
        f"Hybrid search returned {len(unified_results)} results "
        f"(MoEngage: {len(moengage_results)}, History: {len(history_results)})"
    )

    return unified_results


def _deduplicate_results(results: List[UnifiedSearchResult]) -> List[UnifiedSearchResult]:
    """Remove duplicate results based on URL.

    Args:
        results: List of search results

    Returns:
        Deduplicated list
    """
    seen_urls = set()
    deduped = []

    for result in results:
        # Normalize URL for comparison
        url_key = result.url.lower().strip("/")

        if url_key not in seen_urls:
            seen_urls.add(url_key)
            deduped.append(result)

    return deduped


def format_context_for_llm(
    results: List[UnifiedSearchResult],
    current_channel_id: str = ""
) -> str:
    """Format search results as context for LLM.

    Args:
        results: List of search results
        current_channel_id: Current Slack channel ID for filtering history URLs.
                           Pass None to show all URLs (for CSM channels).

    Returns:
        Formatted context string
    """
    if not results:
        return "검색된 관련 문서가 없습니다."

    context_parts = []

    for i, result in enumerate(results, 1):
        if result.source == "support_history":
            source_label = "이전 Q&A"
            # Show URL if:
            # 1. current_channel_id is None (CSM channel - full access)
            # 2. no channel_id stored (backward compatibility)
            # 3. same channel
            result_channel_id = result.metadata.get("channel_id", "")
            if current_channel_id is None or not result_channel_id or result_channel_id == current_channel_id:
                url_info = f"슬랙 URL: {result.url}"
            else:
                url_info = "(다른 채널의 Q&A)"
        else:
            source_label = "MoEngage HelpCenter"
            url_info = f"URL: {result.url}"

        context_parts.append(f"""
---
[문서 {i}] ({source_label})
제목: {result.title}
{url_info}
내용:
{result.content}
---
        """.strip())

    return "\n\n".join(context_parts)


async def search_and_format(query: str) -> Tuple[List[UnifiedSearchResult], str]:
    """Search and return both results and formatted context.

    Args:
        query: Search query

    Returns:
        Tuple of (results, formatted_context)
    """
    results = await hybrid_search(query)
    context = format_context_for_llm(results)
    return results, context
