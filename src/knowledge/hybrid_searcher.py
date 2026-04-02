"""Hybrid searcher combining MoEngage API, History RAG, Slack, and Notion."""

import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from src.knowledge.moengage_api import search_moengage, search_developer_docs, search_partner_docs, SearchResult
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
    use_llm_optimization: bool = True,
    query_analysis: Optional[dict] = None
) -> List[UnifiedSearchResult]:
    """Perform hybrid search across MoEngage API, Slack, and Notion.

    Uses source-specific queries for optimal results:
    - MoEngage: English query (search_query from analyze_query)
    - Slack: Korean keywords (search_keywords from analyze_query)
    - Notion: Korean keywords (search_keywords from analyze_query)

    Args:
        query: Search query (fallback for all sources)
        moengage_top_k: Number of MoEngage results
        history_top_k: Number of History results
        prioritize_history: Whether to prioritize history results
        use_llm_optimization: Whether to use LLM to optimize search query
        query_analysis: Pre-computed analyze_query() result (avoids duplicate LLM call)

    Returns:
        List of unified search results
    """
    from config.settings import settings
    logger.info(f"Hybrid search for: '{query}'")

    # Optimize query using LLM if enabled (skip if already provided)
    if query_analysis is None and use_llm_optimization:
        try:
            query_analysis = await analyze_query(query)
        except Exception as e:
            logger.warning(f"Query optimization failed: {e}")

    # Build source-specific queries
    if query_analysis:
        moengage_query = query_analysis.get("search_query", query)
        keywords = query_analysis.get("search_keywords", [])
        intent = query_analysis.get("intent", "")
        # For Slack/Notion: prefer Korean intent summary, fallback to keywords
        if intent and any('\uac00' <= c <= '\ud7a3' for c in intent):
            korean_query = intent
        elif keywords:
            korean_query = " ".join(keywords)
        else:
            korean_query = query
        logger.info(f"[QUERY] MoEngage: '{moengage_query}' | Slack/Notion: '{korean_query}'")
    else:
        moengage_query = query
        korean_query = query
        logger.info(f"[QUERY] All sources: '{query}'")

    # Build parallel search tasks with source-specific queries
    tasks = [
        search_moengage(moengage_query, top_k=moengage_top_k),
        search_developer_docs(moengage_query, top_k=3),
        search_partner_docs(moengage_query, top_k=3),
    ]
    task_names = ["moengage", "developer", "partner"]

    # Add Slack search if configured (Korean keywords)
    from src.knowledge.slack_searcher import get_slack_searcher
    slack_searcher = get_slack_searcher()
    if slack_searcher:
        tasks.append(slack_searcher.search(korean_query, top_k=settings.slack_search_top_k))
        task_names.append("slack")

    # Add Notion search if configured (Korean keywords)
    from src.knowledge.notion_searcher import get_notion_searcher
    notion_searcher = get_notion_searcher()
    if notion_searcher:
        tasks.append(notion_searcher.search(korean_query, top_k=settings.notion_search_top_k))
        task_names.append("notion")

    # Run all searches in parallel
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results by task
    results_by_source: Dict[str, list] = {}
    for name, result in zip(task_names, raw_results):
        if isinstance(result, Exception):
            logger.error(f"{name} search failed: {result}")
            results_by_source[name] = []
        else:
            results_by_source[name] = result or []
            logger.info(f"[HYBRID] {name} returned {len(results_by_source[name])} results")

    moengage_results = results_by_source.get("moengage", [])
    developer_results = results_by_source.get("developer", [])
    partner_results = results_by_source.get("partner", [])
    slack_results = results_by_source.get("slack", [])
    notion_results = results_by_source.get("notion", [])

    # Convert to unified results
    unified_results: List[UnifiedSearchResult] = []

    # Add MoEngage/Developer/Partner results with rank-based scoring
    for doc_results in [moengage_results, developer_results, partner_results]:
        for i, mr in enumerate(doc_results):
            score = max(0.7, 0.9 - (i * 0.03))
            unified_results.append(UnifiedSearchResult(
                title=mr.title,
                url=mr.url,
                content=mr.content,
                snippet=mr.snippet,
                source=mr.source,
                score=score,
                metadata={"rank": i + 1}
            ))

    # Add Slack results (already UnifiedSearchResult from SlackSearcher)
    unified_results.extend(slack_results)

    # Add Notion results (already UnifiedSearchResult from NotionSearcher)
    unified_results.extend(notion_results)

    # Deduplicate by URL
    unified_results = _deduplicate_results(unified_results)

    logger.info(
        f"Hybrid search returned {len(unified_results)} results "
        f"(UserGuide: {len(moengage_results)}, DevGuide: {len(developer_results)}, "
        f"PartnerGuide: {len(partner_results)}, Slack: {len(slack_results)}, Notion: {len(notion_results)})"
    )

    return unified_results


async def multi_query_hybrid_search(
    query_analysis: dict,
    moengage_top_k: int = 5,
    history_top_k: int = 3,
) -> List[UnifiedSearchResult]:
    """Run hybrid_search for each sub-question in parallel and merge results.

    When a query has multiple sub-questions (e.g. 3 parts), runs separate
    searches for each to find purpose-specific documents, then deduplicates.
    Results found by multiple queries get a score boost.

    Args:
        query_analysis: Result from analyze_query() with sub_questions
        moengage_top_k: Number of MoEngage results per sub-query
        history_top_k: Number of History results per sub-query

    Returns:
        Merged and deduplicated list of search results
    """
    sub_questions = query_analysis.get("sub_questions", [])
    if not sub_questions:
        # Fallback to single search
        query = query_analysis.get("search_query", "")
        return await hybrid_search(
            query, moengage_top_k=moengage_top_k,
            history_top_k=history_top_k, query_analysis=query_analysis
        )

    # Build per-sub-question query_analysis dicts (reuse shared fields)
    search_tasks = []
    for sq in sub_questions:
        sq_analysis = {
            "search_query": sq.get("search_query", query_analysis.get("search_query", "")),
            "search_keywords": query_analysis.get("search_keywords", []),
            "intent": sq.get("question", query_analysis.get("intent", "")),
        }
        search_tasks.append(
            hybrid_search(
                sq_analysis["search_query"],
                moengage_top_k=moengage_top_k,
                history_top_k=history_top_k,
                query_analysis=sq_analysis,
            )
        )

    logger.info(f"[MULTI-QUERY] Running {len(search_tasks)} parallel searches")
    all_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # Merge results: track URL frequency for score boosting
    url_count: Dict[str, int] = {}
    merged: List[UnifiedSearchResult] = []
    seen_urls: set = set()

    for i, result_set in enumerate(all_results):
        if isinstance(result_set, Exception):
            logger.error(f"[MULTI-QUERY] Sub-query {i} failed: {result_set}")
            continue
        for r in result_set:
            url_key = r.url.lower().strip("/")
            url_count[url_key] = url_count.get(url_key, 0) + 1
            if url_key not in seen_urls:
                seen_urls.add(url_key)
                merged.append(r)

    # Boost score for results found by multiple sub-queries
    for r in merged:
        url_key = r.url.lower().strip("/")
        count = url_count.get(url_key, 1)
        if count > 1:
            r.score = min(1.0, r.score + 0.1 * (count - 1))

    logger.info(
        f"[MULTI-QUERY] Merged {sum(len(r) for r in all_results if not isinstance(r, Exception))} "
        f"→ {len(merged)} unique results"
    )
    return merged


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

    # Evidence type labels for LLM context
    _EVIDENCE_LABELS = {
        "direct": "직접 근거",
        "supplementary": "보완 근거",
        "best_practice": "운영 가이드",
    }

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
        elif result.source == "developer_docs":
            source_label = "MoEngage Developer Guide"
            url_info = f"URL: {result.url}"
        elif result.source == "partner_docs":
            source_label = "MoEngage Partner Guide"
            url_info = f"URL: {result.url}"
        else:
            source_label = "MoEngage HelpCenter"
            url_info = f"URL: {result.url}"

        # Add evidence type tag if available
        evidence_type = result.metadata.get("evidence_type", "")
        evidence_tag = f" [{_EVIDENCE_LABELS.get(evidence_type, '')}]" if evidence_type in _EVIDENCE_LABELS else ""

        context_parts.append(f"""
---
[문서 {i}] ({source_label}){evidence_tag}
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


async def rerank_results(
    query: str,
    results: List[UnifiedSearchResult],
    relevance_threshold: int = 5,
    max_results: int = 10
) -> List[UnifiedSearchResult]:
    """Use Claude to re-rank search results by relevance and generate summaries.

    Args:
        query: Original customer query
        results: Raw search results from hybrid_search
        relevance_threshold: Minimum score to include (0-10)
        max_results: Maximum results to return

    Returns:
        Filtered and re-ranked results with updated snippets
    """
    if not results:
        return results

    from src.llm.claude_client import get_claude_client
    from src.llm.prompts import get_rerank_system_prompt, get_rerank_prompt

    # Cap input to avoid exceeding token limits (multi-query can produce 50+ results)
    MAX_RERANK_INPUT = 25
    if len(results) > MAX_RERANK_INPUT:
        logger.info(f"[RERANK] Capping input from {len(results)} to {MAX_RERANK_INPUT}")
        results = results[:MAX_RERANK_INPUT]

    # Build results text for Claude evaluation
    results_text_parts = []
    for i, r in enumerate(results, 1):
        source_label = {
            "moengage_docs": "MoEngage 문서",
            "slack_thread": "Slack 스레드",
            "notion": "Notion 페이지",
            "support_history": "지원 히스토리",
        }.get(r.source, r.source)

        content_preview = (r.content or r.snippet or "")[:300]
        results_text_parts.append(
            f"[결과 {i}] {r.title} ({source_label})\n{content_preview}"
        )

    results_text = "\n\n".join(results_text_parts)
    prompt = get_rerank_prompt(query, results_text)

    try:
        claude = get_claude_client()
        response = await claude.async_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=get_rerank_system_prompt(),
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text if response.content else "[]"

        # Parse JSON response
        from src.llm.claude_client import extract_json_safely
        rankings = extract_json_safely(response_text)

        if not isinstance(rankings, list):
            logger.warning("[RERANK] Invalid response format, returning original results")
            return results[:max_results]

        # Apply rankings
        reranked = []
        for rank_item in rankings:
            idx = rank_item.get("index", 0) - 1  # Convert to 0-based
            relevance = rank_item.get("relevance", 0)
            summary = rank_item.get("summary", "")
            evidence_type = rank_item.get("evidence_type", "")

            if 0 <= idx < len(results) and relevance >= relevance_threshold:
                result = results[idx]
                result.score = relevance / 10.0  # Normalize to 0-1
                if summary:
                    result.snippet = summary
                if evidence_type:
                    result.metadata["evidence_type"] = evidence_type
                reranked.append(result)

        # Sort by score descending
        reranked.sort(key=lambda r: r.score, reverse=True)

        logger.info(
            f"[RERANK] {len(results)} → {len(reranked)} results "
            f"(threshold: {relevance_threshold})"
        )
        return reranked[:max_results]

    except Exception as e:
        logger.error(f"[RERANK] Re-ranking failed, returning original: {e}")
        return results[:max_results]


def group_results_by_source(
    results: List[UnifiedSearchResult]
) -> Dict[str, List[UnifiedSearchResult]]:
    """Group search results by source type.

    Returns dict with keys: moengage_docs, support_history, slack_thread, notion
    """
    grouped: Dict[str, List[UnifiedSearchResult]] = {
        "support_history": [],
        "moengage_docs": [],
        "slack_thread": [],
        "notion": [],
    }
    for r in results:
        source = r.source
        if source in grouped:
            grouped[source].append(r)
        else:
            grouped[source] = [r]
    return grouped
