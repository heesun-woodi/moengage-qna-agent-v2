"""MoEngage Help Center API client using Zendesk Search API."""

import asyncio
from typing import List, Optional
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from cachetools import TTLCache

from config.settings import settings
from src.utils.logger import logger
from src.utils.retry import retry_moengage_api, moengage_circuit
from src.utils.term_mapper import translate_for_search


@dataclass
class SearchResult:
    """Search result from MoEngage Help Center."""
    title: str
    url: str
    content: str
    snippet: str
    source: str = "moengage_docs"


# Cache for search results (TTL from settings)
_cache: TTLCache = TTLCache(
    maxsize=100,
    ttl=settings.moengage_api_cache_ttl
)


def _html_to_text(html_content: str, max_length: int = 2000) -> str:
    """Convert HTML content to plain text.

    Args:
        html_content: HTML string
        max_length: Maximum text length to return

    Returns:
        Plain text extracted from HTML
    """
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "lxml")

    # Remove script and style elements
    for element in soup(["script", "style", "nav", "footer"]):
        element.decompose()

    # Get text with proper spacing
    text = soup.get_text(separator="\n", strip=True)

    # Clean up multiple newlines
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = "\n".join(lines)

    # Truncate if needed
    if len(text) > max_length:
        text = text[:max_length] + "..."

    return text


@retry_moengage_api
@moengage_circuit
async def search_moengage(
    query: str,
    top_k: Optional[int] = None,
    translate_query: bool = True
) -> List[SearchResult]:
    """Search MoEngage Help Center using Zendesk Search API.

    Args:
        query: Search query (Korean or English)
        top_k: Number of results to return
        translate_query: Whether to translate Korean query to English

    Returns:
        List of SearchResult objects
    """
    top_k = top_k or settings.moengage_search_top_k

    # Translate Korean query to English for better results
    search_query = query
    if translate_query:
        english_query = translate_for_search(query)
        if english_query != query:
            # Use both original and translated for broader search
            search_query = f"{query} {english_query}"
            logger.debug(f"Expanded query: {query} -> {search_query}")

    # Check cache
    cache_key = f"{search_query}:{top_k}"
    if cache_key in _cache:
        logger.debug(f"Cache hit for query: {query}")
        return _cache[cache_key]

    # Call Zendesk Search API
    url = f"{settings.moengage_help_center_url}/api/v2/help_center/articles/search.json"
    params = {
        "query": search_query,
        "per_page": top_k
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    results: List[SearchResult] = []
    for article in data.get("results", []):
        # Extract and clean content
        content = _html_to_text(article.get("body", ""))

        # Skip empty or minimal content
        if len(content) < 50:
            continue

        result = SearchResult(
            title=article.get("title", ""),
            url=article.get("html_url", ""),
            content=content,
            snippet=article.get("snippet", "")[:200] if article.get("snippet") else content[:200],
            source="moengage_docs"
        )
        results.append(result)

    # Cache results
    _cache[cache_key] = results

    logger.info(f"MoEngage API: Found {len(results)} results for '{query}'")
    return results


async def get_article_by_id(article_id: int) -> Optional[SearchResult]:
    """Fetch a specific article by ID.

    Args:
        article_id: Zendesk article ID

    Returns:
        SearchResult or None if not found
    """
    url = f"{settings.moengage_help_center_url}/api/v2/help_center/articles/{article_id}.json"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        article = data.get("article", {})
        content = _html_to_text(article.get("body", ""))

        return SearchResult(
            title=article.get("title", ""),
            url=article.get("html_url", ""),
            content=content,
            snippet=content[:200],
            source="moengage_docs"
        )
    except Exception as e:
        logger.error(f"Failed to fetch article {article_id}: {e}")
        return None


def clear_cache():
    """Clear the search cache."""
    _cache.clear()
    logger.info("MoEngage API cache cleared")


# Synchronous wrapper for non-async contexts
def search_moengage_sync(query: str, top_k: Optional[int] = None) -> List[SearchResult]:
    """Synchronous version of search_moengage."""
    return asyncio.run(search_moengage(query, top_k))
