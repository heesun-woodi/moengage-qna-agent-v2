"""Storage backends for FAISS indices and metadata."""

from src.storage.base import StorageBackend
from src.storage.local import LocalStorage

# GCS is optional - only import if google-cloud-storage is available
try:
    from src.storage.gcs import GCSStorage
    GCS_AVAILABLE = True
except ImportError:
    GCSStorage = None
    GCS_AVAILABLE = False

from config.settings import settings


def get_storage_backend() -> StorageBackend:
    """Get the configured storage backend.

    Returns:
        StorageBackend instance based on STORAGE_BACKEND env var
    """
    backend_type = settings.storage_backend.lower()

    if backend_type == "gcs":
        if not GCS_AVAILABLE:
            raise RuntimeError(
                "GCS storage requested but google-cloud-storage is not installed. "
                "Install with: pip install google-cloud-storage"
            )
        if not settings.gcp_storage_bucket:
            raise ValueError("GCP_STORAGE_BUCKET is required for GCS backend")
        return GCSStorage(bucket_name=settings.gcp_storage_bucket)

    # Default to local storage
    return LocalStorage(base_path=settings.chroma_persist_dir)


__all__ = [
    "StorageBackend",
    "LocalStorage",
    "GCSStorage",
    "GCS_AVAILABLE",
    "get_storage_backend",
]
