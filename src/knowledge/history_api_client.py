"""History API Client - For posting entries to remote Railway instance."""

import aiohttp
from typing import Optional, Dict, Any
from dataclasses import asdict

from config.settings import settings
from src.knowledge.history_rag import HistoryEntry
from src.utils.logger import logger


class HistoryAPIClient:
    """Client for remote History API."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize API client.

        Args:
            base_url: API base URL (e.g., https://moengage-qna-agent.up.railway.app)
            api_key: API key for authentication
        """
        self.base_url = (base_url or settings.history_api_url).rstrip("/")
        self.api_key = api_key or settings.history_api_key

        if not self.base_url:
            raise ValueError(
                "HISTORY_API_URL is not configured. "
                "Set it in .env or pass base_url parameter."
            )
        if not self.api_key:
            raise ValueError(
                "HISTORY_API_KEY is not configured. "
                "Set it in .env or pass api_key parameter."
            )

    async def add_entry(self, entry: HistoryEntry) -> Optional[str]:
        """Add a history entry via API.

        Args:
            entry: HistoryEntry to add

        Returns:
            Entry ID if successful, None otherwise
        """
        url = f"{self.base_url}/api/history"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # Convert entry to dict
        data = asdict(entry)
        # Remove id (will be auto-generated server-side)
        data.pop("id", None)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        entry_id = result.get("entry_id")
                        logger.info(
                            f"Remote API: Added entry '{entry.title}' (ID: {entry_id})"
                        )
                        return entry_id
                    else:
                        error = await response.text()
                        logger.error(
                            f"Remote API error: {response.status} - {error}"
                        )
                        return None

        except aiohttp.ClientError as e:
            logger.error(f"Remote API connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"Remote API request failed: {e}")
            return None

    async def get_stats(self) -> Optional[Dict[str, Any]]:
        """Get history statistics from remote API.

        Returns:
            Stats dict or None if failed
        """
        url = f"{self.base_url}/api/history/stats"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        logger.error(f"Remote API stats error: {error}")
                        return None

        except Exception as e:
            logger.error(f"Remote API stats failed: {e}")
            return None

    async def list_entries(self) -> Optional[Dict[str, Any]]:
        """List all history entries from remote API.

        Returns:
            Dict with entries list or None if failed
        """
        url = f"{self.base_url}/api/history"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        logger.error(f"Remote API list error: {error}")
                        return None

        except Exception as e:
            logger.error(f"Remote API list failed: {e}")
            return None

    async def health_check(self) -> bool:
        """Check if remote API is healthy.

        Returns:
            True if healthy, False otherwise
        """
        url = f"{self.base_url}/api/health"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return response.status == 200

        except Exception:
            return False


def get_history_api_client() -> Optional[HistoryAPIClient]:
    """Get API client if configured.

    Returns:
        HistoryAPIClient or None if not configured
    """
    if settings.history_api_url and settings.history_api_key:
        try:
            return HistoryAPIClient()
        except ValueError as e:
            logger.warning(f"Cannot create API client: {e}")
            return None
    return None


def is_remote_api_configured() -> bool:
    """Check if remote API is configured.

    Returns:
        True if both URL and key are set
    """
    return bool(settings.history_api_url and settings.history_api_key)
