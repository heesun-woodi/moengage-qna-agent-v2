"""Local filesystem storage backend."""

from pathlib import Path
from typing import Optional

from src.storage.base import StorageBackend
from src.utils.logger import logger


class LocalStorage(StorageBackend):
    """Local filesystem storage backend.

    This is the default storage backend that stores files in the local filesystem.
    Used for development and single-instance deployments.
    """

    def __init__(self, base_path: str):
        """Initialize local storage.

        Args:
            base_path: Base directory for all storage operations
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized at {self.base_path}")

    def _resolve_path(self, path: str) -> Path:
        """Resolve a relative path to an absolute path.

        Args:
            path: Relative path within the storage

        Returns:
            Absolute path
        """
        return self.base_path / path

    def exists(self, path: str) -> bool:
        """Check if a file exists.

        Args:
            path: Relative path within the storage

        Returns:
            True if the file exists
        """
        return self._resolve_path(path).exists()

    def read_bytes(self, path: str) -> Optional[bytes]:
        """Read raw bytes from storage.

        Args:
            path: Relative path within the storage

        Returns:
            File contents as bytes, or None if not found
        """
        file_path = self._resolve_path(path)
        try:
            if not file_path.exists():
                return None
            return file_path.read_bytes()
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return None

    def write_bytes(self, path: str, data: bytes) -> bool:
        """Write raw bytes to storage.

        Args:
            path: Relative path within the storage
            data: Bytes to write

        Returns:
            True if successful
        """
        file_path = self._resolve_path(path)
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            return True
        except Exception as e:
            logger.error(f"Failed to write {file_path}: {e}")
            return False

    def delete(self, path: str) -> bool:
        """Delete a file from storage.

        Args:
            path: Relative path within the storage

        Returns:
            True if deleted or didn't exist
        """
        file_path = self._resolve_path(path)
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False

    def list_files(self, prefix: str = "") -> list:
        """List files in storage with given prefix.

        Args:
            prefix: Path prefix to filter by

        Returns:
            List of relative file paths
        """
        try:
            base = self._resolve_path(prefix) if prefix else self.base_path
            if not base.exists():
                return []

            if base.is_file():
                return [prefix]

            files = []
            for path in base.rglob("*"):
                if path.is_file():
                    rel_path = path.relative_to(self.base_path)
                    files.append(str(rel_path))
            return sorted(files)
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []
