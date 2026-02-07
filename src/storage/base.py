"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from typing import Optional
import json


class StorageBackend(ABC):
    """Abstract storage backend for FAISS indices and metadata.

    Implementations:
    - LocalStorage: Local filesystem (default)
    - GCSStorage: Google Cloud Storage
    """

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists.

        Args:
            path: Relative path within the storage

        Returns:
            True if the file exists
        """
        pass

    @abstractmethod
    def read_bytes(self, path: str) -> Optional[bytes]:
        """Read raw bytes from storage.

        Args:
            path: Relative path within the storage

        Returns:
            File contents as bytes, or None if not found
        """
        pass

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> bool:
        """Write raw bytes to storage.

        Args:
            path: Relative path within the storage
            data: Bytes to write

        Returns:
            True if successful
        """
        pass

    def read_json(self, path: str) -> Optional[dict]:
        """Read JSON file from storage.

        Args:
            path: Relative path within the storage

        Returns:
            Parsed JSON as dict, or None if not found
        """
        data = self.read_bytes(path)
        if data is None:
            return None
        try:
            return json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def write_json(self, path: str, data: dict) -> bool:
        """Write JSON file to storage.

        Args:
            path: Relative path within the storage
            data: Dictionary to write as JSON

        Returns:
            True if successful
        """
        try:
            json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
            return self.write_bytes(path, json_bytes)
        except (TypeError, ValueError):
            return False

    @abstractmethod
    def delete(self, path: str) -> bool:
        """Delete a file from storage.

        Args:
            path: Relative path within the storage

        Returns:
            True if deleted or didn't exist
        """
        pass

    @abstractmethod
    def list_files(self, prefix: str = "") -> list:
        """List files in storage with given prefix.

        Args:
            prefix: Path prefix to filter by

        Returns:
            List of file paths
        """
        pass
