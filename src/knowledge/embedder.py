"""Shared embedding model loader.

Caches the SentenceTransformer model instance so it's loaded once
and reused across history_rag and learning_store.
"""

from src.utils.logger import logger

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384

_cached_embedder = None


def get_embedder():
    """Get or create the shared SentenceTransformer instance."""
    global _cached_embedder
    if _cached_embedder is None:
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise RuntimeError(
                "sentence-transformers is not available. "
                "Install with: pip install sentence-transformers"
            )
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _cached_embedder = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Embedding model loaded successfully")
    return _cached_embedder
