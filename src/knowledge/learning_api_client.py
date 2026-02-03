"""Learning API Client - For posting learning entries to remote Railway instance."""

import aiohttp
from typing import Optional, Dict, Any

from config.settings import settings
from src.utils.logger import logger


class LearningAPIClient:
    """Client for remote Learning API."""

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

    async def add_entry(self, entry_dict: Dict[str, Any]) -> Optional[str]:
        """Add a learning entry via API.

        Args:
            entry_dict: LearningEntry as dictionary

        Returns:
            Entry ID if successful, None otherwise
        """
        url = f"{self.base_url}/api/learning"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # Remove id (will be auto-generated server-side if empty)
        data = dict(entry_dict)
        if not data.get("id"):
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
                            f"Remote Learning API: Added entry (ID: {entry_id})"
                        )
                        return entry_id
                    else:
                        error = await response.text()
                        logger.error(
                            f"Remote Learning API error: {response.status} - {error}"
                        )
                        return None

        except aiohttp.ClientError as e:
            logger.error(f"Remote Learning API connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"Remote Learning API request failed: {e}")
            return None

    async def get_stats(self) -> Optional[Dict[str, Any]]:
        """Get learning statistics from remote API.

        Returns:
            Stats dict or None if failed
        """
        url = f"{self.base_url}/api/learning/stats"
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
                        logger.error(f"Remote Learning API stats error: {error}")
                        return None

        except Exception as e:
            logger.error(f"Remote Learning API stats failed: {e}")
            return None

    async def list_entries(self, limit: int = 100) -> Optional[Dict[str, Any]]:
        """List learning entries from remote API.

        Args:
            limit: Maximum number of entries to return

        Returns:
            Dict with entries list or None if failed
        """
        url = f"{self.base_url}/api/learning?limit={limit}"
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
                        logger.error(f"Remote Learning API list error: {error}")
                        return None

        except Exception as e:
            logger.error(f"Remote Learning API list failed: {e}")
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


def get_learning_api_client() -> Optional[LearningAPIClient]:
    """Get API client if configured.

    Returns:
        LearningAPIClient or None if not configured
    """
    if settings.history_api_url and settings.history_api_key:
        try:
            return LearningAPIClient()
        except ValueError as e:
            logger.warning(f"Cannot create Learning API client: {e}")
            return None
    return None


def is_learning_api_configured() -> bool:
    """Check if remote Learning API is configured.

    Returns:
        True if both URL and key are set
    """
    return bool(settings.history_api_url and settings.history_api_key)
