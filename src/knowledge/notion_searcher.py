"""Notion document searcher using Notion API."""

from typing import List, Optional

from src.utils.logger import logger


class NotionSearcher:
    """Search Notion pages using Notion API."""

    def __init__(self, token: str, db_ids: Optional[List[str]] = None):
        """Initialize Notion searcher.

        Args:
            token: Notion Integration Token (secret_...)
            db_ids: Optional list of Database IDs to restrict search to
        """
        from notion_client import AsyncClient
        self.client = AsyncClient(auth=token)
        self.db_ids = db_ids or []

    def _extract_page_text(self, blocks: list) -> str:
        """Extract plain text from Notion block objects."""
        texts = []
        for block in blocks:
            block_type = block.get("type", "")
            block_data = block.get(block_type, {})

            # Handle rich_text arrays (paragraph, heading, bulleted_list_item, etc.)
            rich_texts = block_data.get("rich_text", [])
            if rich_texts:
                line = "".join(rt.get("plain_text", "") for rt in rich_texts)
                if line.strip():
                    texts.append(line)

        return "\n".join(texts)

    def _get_page_url(self, page: dict) -> str:
        """Get the URL of a Notion page."""
        page_url = page.get("url", "")
        if page_url:
            return page_url
        # Fallback: construct from ID
        page_id = page.get("id", "").replace("-", "")
        return f"https://www.notion.so/{page_id}"

    def _get_page_title(self, page: dict) -> str:
        """Extract title from a Notion page."""
        # Check properties for title
        properties = page.get("properties", {})
        for prop_name, prop_data in properties.items():
            prop_type = prop_data.get("type", "")
            if prop_type == "title":
                rich_texts = prop_data.get("title", [])
                title = "".join(rt.get("plain_text", "") for rt in rich_texts)
                if title.strip():
                    return title.strip()

        return "Untitled"

    async def search(self, query: str, top_k: int = 5) -> list:
        """Search Notion pages.

        Args:
            query: Search query string
            top_k: Maximum number of results to return

        Returns:
            List of UnifiedSearchResult objects
        """
        from src.knowledge.hybrid_searcher import UnifiedSearchResult

        results = []

        try:
            # Workspace search finds all connected pages and databases
            results = await self._workspace_search(query, top_k)
        except Exception as e:
            logger.error(f"[NOTION SEARCH] Search failed: {e}")

        return results[:top_k]

    async def _query_database(self, db_id: str, query: str, top_k: int) -> list:
        """Query a specific Notion database."""
        results = []
        try:
            response = await self.client.databases.query(
                database_id=db_id,
                filter={"property": "title", "title": {"contains": query}},
                page_size=top_k
            )
            pages = response.get("results", [])
            logger.info(f"[NOTION SEARCH] DB {db_id[:8]}: {len(pages)} results")
            for page in pages:
                result = await self._process_page(page)
                if result:
                    results.append(result)
        except Exception as e:
            logger.warning(f"[NOTION SEARCH] DB query failed for {db_id[:8]}: {e}")
        return results

    async def _workspace_search(self, query: str, top_k: int) -> list:
        """Search across entire Notion workspace."""
        from src.knowledge.hybrid_searcher import UnifiedSearchResult

        results = []
        try:
            response = await self.client.search(
                query=query,
                filter={"property": "object", "value": "page"},
                page_size=top_k
            )
            pages = response.get("results", [])
            logger.info(f"[NOTION SEARCH] Workspace search: {len(pages)} results")

            for page in pages:
                result = await self._process_page(page)
                if result:
                    results.append(result)
        except Exception as e:
            logger.error(f"[NOTION SEARCH] Workspace search failed: {e}")

        return results

    async def _process_page(self, page: dict):
        """Process a Notion page into a UnifiedSearchResult."""
        from src.knowledge.hybrid_searcher import UnifiedSearchResult

        try:
            title = self._get_page_title(page)
            url = self._get_page_url(page)
            page_id = page.get("id", "")

            # Fetch page blocks for content
            content = ""
            try:
                blocks_response = await self.client.blocks.children.list(
                    block_id=page_id,
                    page_size=20
                )
                blocks = blocks_response.get("results", [])
                content = self._extract_page_text(blocks)
            except Exception as e:
                logger.debug(f"Could not fetch blocks for page {page_id[:8]}: {e}")

            snippet = content[:300].strip() if content else title
            logger.info(f"[NOTION SEARCH] Found: {title} | {url}")

            return UnifiedSearchResult(
                title=title,
                url=url,
                content=content[:2000],
                snippet=snippet,
                source="notion",
                score=0.8,
                metadata={
                    "page_id": page_id,
                    "last_edited": page.get("last_edited_time", ""),
                }
            )
        except Exception as e:
            logger.debug(f"[NOTION SEARCH] Failed to process page: {e}")
            return None


# Singleton instance
_notion_searcher: Optional[NotionSearcher] = None


def get_notion_searcher() -> Optional[NotionSearcher]:
    """Get or create Notion searcher singleton.

    Returns None if NOTION_TOKEN is not configured.
    """
    global _notion_searcher
    if _notion_searcher is not None:
        return _notion_searcher

    from config.settings import settings
    if not settings.notion_token:
        return None

    _notion_searcher = NotionSearcher(
        token=settings.notion_token,
        db_ids=settings.notion_db_id_list
    )
    logger.info(
        f"[NOTION SEARCH] Initialized with "
        f"{len(settings.notion_db_id_list)} DB filters"
    )
    return _notion_searcher
