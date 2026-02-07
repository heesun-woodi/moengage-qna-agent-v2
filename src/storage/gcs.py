"""Google Cloud Storage backend."""

from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound

from src.storage.base import StorageBackend
from src.utils.logger import logger


class GCSStorage(StorageBackend):
    """Google Cloud Storage backend.

    Stores files in a GCS bucket for distributed deployments.
    Requires google-cloud-storage package and proper authentication.

    Authentication options:
    1. GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON
    2. Default credentials (when running on GCP)
    3. gcloud CLI authentication (for local development)
    """

    def __init__(self, bucket_name: str, prefix: str = ""):
        """Initialize GCS storage.

        Args:
            bucket_name: GCS bucket name
            prefix: Optional prefix for all paths (e.g., "moengage-qna/")
        """
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        logger.info(f"GCSStorage initialized: gs://{bucket_name}/{self.prefix}")

    def _resolve_path(self, path: str) -> str:
        """Resolve a relative path to a GCS blob path.

        Args:
            path: Relative path within the storage

        Returns:
            Full blob path including prefix
        """
        return f"{self.prefix}{path}"

    def exists(self, path: str) -> bool:
        """Check if a file exists in GCS.

        Args:
            path: Relative path within the storage

        Returns:
            True if the blob exists
        """
        blob_path = self._resolve_path(path)
        blob = self.bucket.blob(blob_path)
        return blob.exists()

    def read_bytes(self, path: str) -> Optional[bytes]:
        """Read raw bytes from GCS.

        Args:
            path: Relative path within the storage

        Returns:
            Blob contents as bytes, or None if not found
        """
        blob_path = self._resolve_path(path)
        blob = self.bucket.blob(blob_path)
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None
        except Exception as e:
            logger.error(f"Failed to read gs://{self.bucket.name}/{blob_path}: {e}")
            return None

    def write_bytes(self, path: str, data: bytes) -> bool:
        """Write raw bytes to GCS.

        Args:
            path: Relative path within the storage
            data: Bytes to write

        Returns:
            True if successful
        """
        blob_path = self._resolve_path(path)
        blob = self.bucket.blob(blob_path)
        try:
            blob.upload_from_string(data)
            logger.debug(f"Wrote {len(data)} bytes to gs://{self.bucket.name}/{blob_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write gs://{self.bucket.name}/{blob_path}: {e}")
            return False

    def delete(self, path: str) -> bool:
        """Delete a file from GCS.

        Args:
            path: Relative path within the storage

        Returns:
            True if deleted or didn't exist
        """
        blob_path = self._resolve_path(path)
        blob = self.bucket.blob(blob_path)
        try:
            blob.delete()
            return True
        except NotFound:
            return True  # Already doesn't exist
        except Exception as e:
            logger.error(f"Failed to delete gs://{self.bucket.name}/{blob_path}: {e}")
            return False

    def list_files(self, prefix: str = "") -> list:
        """List files in GCS with given prefix.

        Args:
            prefix: Path prefix to filter by

        Returns:
            List of relative file paths (without the storage prefix)
        """
        try:
            full_prefix = self._resolve_path(prefix)
            blobs = self.bucket.list_blobs(prefix=full_prefix)

            files = []
            for blob in blobs:
                # Remove the storage prefix to get relative path
                if self.prefix and blob.name.startswith(self.prefix):
                    rel_path = blob.name[len(self.prefix):]
                else:
                    rel_path = blob.name
                files.append(rel_path)

            return sorted(files)
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []
