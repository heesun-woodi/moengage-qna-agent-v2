"""History RAG - FAISS based persistent vector storage for support history."""

import hashlib
import json
import tempfile
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import numpy as np

# Try to import FAISS
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None

from config.settings import settings
from src.utils.logger import logger
from src.storage import get_storage_backend, StorageBackend
from src.knowledge.embedder import get_embedder, SENTENCE_TRANSFORMERS_AVAILABLE


@dataclass
class HistoryEntry:
    """Support history entry."""
    id: str
    title: str
    customer: str
    category: str
    query_summary: str
    solution: str
    created_at: str
    url: str = ""
    channel_id: str = ""
    channel_name: str = ""
    referenced_docs: List[str] = field(default_factory=list)
    referenced_history: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "support_history"
    source_type: str = "customer"  # "customer" | "csm"


@dataclass
class HistorySearchResult:
    """Search result from History RAG."""
    entry: HistoryEntry
    score: float
    source: str = "support_history"


class FAISSHistoryRAG:
    """FAISS-based RAG for persistent history storage."""

    # Multilingual model for Korean + English
    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM = 384

    def __init__(self, persist_dir: Optional[str] = None, storage: Optional[StorageBackend] = None):
        """Initialize FAISS History RAG.

        Args:
            persist_dir: Directory to persist data (for local storage)
            storage: Storage backend to use (if None, uses configured backend)
        """
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS is not available. Install with: pip install faiss-cpu")

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise RuntimeError("sentence-transformers is not available. Install with: pip install sentence-transformers")

        # Use provided storage or get configured backend
        self.storage = storage or get_storage_backend()

        # Path prefixes for storage
        self.index_path = "vectordb/faiss.index"
        self.metadata_path = "vectordb/metadata.json"

        # Legacy paths (for local storage migration)
        self.persist_dir = Path(persist_dir or settings.chroma_persist_dir)
        self._legacy_index_path = self.persist_dir / "faiss.index"
        self._legacy_metadata_path = self.persist_dir / "metadata.pkl"
        self._legacy_json_path = self.persist_dir / "metadata.json"

        # Load embedding model (shared instance)
        self.embedder = get_embedder()

        # Load or create index
        self._load_or_create()

        logger.info(f"FAISS History RAG initialized with {self.count()} entries")

    def _load_or_create(self):
        """Load existing index or create new one."""
        # Try to load from storage backend first
        if self.storage.exists(self.index_path) and self.storage.exists(self.metadata_path):
            try:
                # Load FAISS index from bytes
                index_bytes = self.storage.read_bytes(self.index_path)
                if index_bytes:
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.index') as tmp:
                        tmp.write(index_bytes)
                        tmp.flush()
                        self.index = faiss.read_index(tmp.name)

                    # Load metadata
                    data = self.storage.read_json(self.metadata_path)
                    if data:
                        self._parse_metadata(data)
                        logger.info(f"Loaded FAISS index from storage with {self.index.ntotal} vectors")
                        return
            except Exception as e:
                logger.error(f"Failed to load from storage backend: {e}")

        # Try legacy local file paths for migration
        if self._legacy_json_path.exists() and self._legacy_index_path.exists():
            try:
                self.index = faiss.read_index(str(self._legacy_index_path))
                with open(self._legacy_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._parse_metadata(data)
                logger.info(f"Loaded from legacy JSON format, migrating to storage backend...")
                self._save()  # Migrate to storage backend
                return
            except Exception as e:
                logger.error(f"Failed to load legacy JSON: {e}")

        # Try legacy pickle format
        if self._legacy_metadata_path.exists() and self._legacy_index_path.exists():
            if self._try_load_legacy():
                logger.info("Migrated from legacy pickle format")
                self._save()
                return

        # Create new index if nothing found
        self._create_new_index()

    def _parse_metadata(self, data: dict):
        """Parse metadata dict and reconstruct HistoryEntry objects."""
        self.entries: Dict[str, HistoryEntry] = {}
        for k, v in data.get('entries', {}).items():
            if isinstance(v, dict):
                # Add default values for new fields if missing
                v.setdefault('referenced_docs', [])
                v.setdefault('referenced_history', [])
                v.setdefault('metadata', {})
                v.setdefault('source', 'support_history')
                v.setdefault('source_type', 'customer')
                self.entries[k] = HistoryEntry(**v)
            else:
                # Legacy object - add missing attributes
                if not hasattr(v, 'referenced_docs'):
                    v.referenced_docs = []
                if not hasattr(v, 'referenced_history'):
                    v.referenced_history = []
                if not hasattr(v, 'metadata'):
                    v.metadata = {}
                self.entries[k] = v
        self.id_to_idx: Dict[str, int] = data.get('id_to_idx', {})

    def _try_load_legacy(self) -> bool:
        """Try to load legacy pickle format for migration."""
        try:
            import pickle
            if self._legacy_metadata_path.exists():
                self.index = faiss.read_index(str(self.index_path))
                with open(self._legacy_metadata_path, 'rb') as f:
                    data = pickle.load(f)
                    raw_entries = data.get('entries', {})
                    self.entries = {}
                    # Add missing attributes to legacy entries
                    for k, v in raw_entries.items():
                        if not hasattr(v, 'referenced_docs'):
                            v.referenced_docs = []
                        if not hasattr(v, 'referenced_history'):
                            v.referenced_history = []
                        if not hasattr(v, 'metadata'):
                            v.metadata = {}
                        self.entries[k] = v
                    self.id_to_idx = data.get('id_to_idx', {})
                return True
        except Exception as e:
            logger.error(f"Failed to load legacy pickle: {e}")
        return False

    def _create_new_index(self):
        """Create new empty index."""
        # IndexFlatIP for inner product (cosine similarity with normalized vectors)
        self.index = faiss.IndexFlatIP(self.EMBEDDING_DIM)
        self.entries: Dict[str, HistoryEntry] = {}
        self.id_to_idx: Dict[str, int] = {}
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created new FAISS index")

    def _entry_to_dict(self, entry: HistoryEntry) -> dict:
        """Convert HistoryEntry to dict, handling missing attributes."""
        # Try asdict first for proper dataclass instances
        try:
            return asdict(entry)
        except Exception:
            pass

        # Fallback: manually build dict with defaults for missing fields
        return {
            'id': getattr(entry, 'id', ''),
            'title': getattr(entry, 'title', ''),
            'customer': getattr(entry, 'customer', ''),
            'category': getattr(entry, 'category', ''),
            'query_summary': getattr(entry, 'query_summary', ''),
            'solution': getattr(entry, 'solution', ''),
            'created_at': getattr(entry, 'created_at', ''),
            'url': getattr(entry, 'url', ''),
            'channel_id': getattr(entry, 'channel_id', ''),
            'channel_name': getattr(entry, 'channel_name', ''),
            'referenced_docs': getattr(entry, 'referenced_docs', []),
            'referenced_history': getattr(entry, 'referenced_history', []),
            'metadata': getattr(entry, 'metadata', {}),
            'source': getattr(entry, 'source', 'support_history'),
            'source_type': getattr(entry, 'source_type', 'customer'),
        }

    def _save(self):
        """Save index and metadata to storage backend."""
        try:
            # Save FAISS index to temp file then upload bytes
            with tempfile.NamedTemporaryFile(delete=False, suffix='.index') as tmp:
                faiss.write_index(self.index, tmp.name)
                tmp.flush()
                with open(tmp.name, 'rb') as f:
                    index_bytes = f.read()
                self.storage.write_bytes(self.index_path, index_bytes)

            # Convert HistoryEntry objects to dicts for JSON serialization
            entries_dict = {}
            for k, v in self.entries.items():
                if isinstance(v, dict):
                    entries_dict[k] = v
                else:
                    entries_dict[k] = self._entry_to_dict(v)

            # Save metadata JSON
            metadata = {
                'entries': entries_dict,
                'id_to_idx': self.id_to_idx
            }
            self.storage.write_json(self.metadata_path, metadata)

            logger.info(f"Saved FAISS index and metadata to storage ({len(self.entries)} entries)")
        except Exception as e:
            logger.error(f"Failed to save index: {e}")

    def _generate_id(self, content: str) -> str:
        """Generate unique ID from content hash."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _create_document(self, entry: HistoryEntry) -> str:
        """Create searchable document from entry."""
        channel_info = f"채널: {entry.channel_name}\n" if entry.channel_name else ""
        return f"""
제목: {entry.title}
고객사: {entry.customer}
{channel_info}카테고리: {entry.category}
문의 요약: {entry.query_summary}
해결 내용: {entry.solution}
        """.strip()

    def add_entry(self, entry: HistoryEntry) -> str:
        """Add a support history entry.

        Args:
            entry: HistoryEntry to add

        Returns:
            Entry ID
        """
        document = self._create_document(entry)
        entry_id = entry.id or self._generate_id(document)

        # Check for existing entry
        if entry_id in self.entries:
            logger.debug(f"Entry {entry_id} already exists, updating...")
            # For FAISS, we can't easily update, so we'll just update metadata
            entry.id = entry_id
            self.entries[entry_id] = entry
            self._save()
            return entry_id

        # Generate embedding
        embedding = self.embedder.encode([document], normalize_embeddings=True)
        embedding = embedding.astype('float32')

        # Add to index
        idx = self.index.ntotal
        self.index.add(embedding)

        # Store metadata
        entry.id = entry_id
        self.entries[entry_id] = entry
        self.id_to_idx[entry_id] = idx

        # Persist
        self._save()

        logger.info(f"Added history entry: {entry.title} (ID: {entry_id})")
        return entry_id

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_score: float = 0.0,
        filter_customer: Optional[str] = None,
        filter_category: Optional[str] = None
    ) -> List[HistorySearchResult]:
        """Search support history.

        Args:
            query: Search query
            top_k: Number of results to return
            min_score: Minimum similarity score (0-1)
            filter_customer: Filter by customer name
            filter_category: Filter by category

        Returns:
            List of HistorySearchResult
        """
        top_k = top_k or settings.history_search_top_k

        if self.index.ntotal == 0:
            return []

        # Generate query embedding
        query_embedding = self.embedder.encode([query], normalize_embeddings=True)
        query_embedding = query_embedding.astype('float32')

        # Search (get more results for filtering)
        search_k = min(top_k * 3, self.index.ntotal)
        scores, indices = self.index.search(query_embedding, search_k)

        # Build reverse mapping
        idx_to_id = {v: k for k, v in self.id_to_idx.items()}

        results: List[HistorySearchResult] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue

            # Get entry
            entry_id = idx_to_id.get(int(idx))
            if not entry_id or entry_id not in self.entries:
                continue

            entry = self.entries[entry_id]

            # Apply filters
            if filter_customer and entry.customer != filter_customer:
                continue
            if filter_category and entry.category != filter_category:
                continue

            # Check minimum score
            if score < min_score:
                continue

            results.append(HistorySearchResult(
                entry=entry,
                score=float(score),
                source="support_history"
            ))

            if len(results) >= top_k:
                break

        logger.info(f"History RAG: Found {len(results)} results for '{query[:50]}...'")
        return results

    def get_entry(self, entry_id: str) -> Optional[HistoryEntry]:
        """Get an entry by ID."""
        return self.entries.get(entry_id)

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by ID.

        Note: FAISS doesn't support deletion, so we just remove from metadata.
        The vector remains in the index but won't be returned in search results.
        """
        if entry_id in self.entries:
            del self.entries[entry_id]
            if entry_id in self.id_to_idx:
                del self.id_to_idx[entry_id]
            self._save()
            logger.info(f"Deleted history entry: {entry_id}")
            return True
        return False

    def count(self) -> int:
        """Get total number of entries."""
        return len(self.entries)

    def clear(self):
        """Clear all entries."""
        self._create_new_index()
        self._save()
        logger.info("History RAG cleared")


class InMemoryHistoryRAG:
    """In-memory fallback when FAISS is not available."""

    def __init__(self):
        self._entries: Dict[str, HistoryEntry] = {}
        self._embeddings: Dict[str, np.ndarray] = {}
        self._embedder = None
        logger.warning("Using in-memory History RAG (FAISS not available)")

    def _get_embedder(self):
        if self._embedder is None and SENTENCE_TRANSFORMERS_AVAILABLE:
            self._embedder = get_embedder()
        return self._embedder

    def _generate_id(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def add_entry(self, entry: HistoryEntry) -> str:
        document = f"{entry.title} {entry.query_summary} {entry.solution}"
        entry_id = entry.id or self._generate_id(document)
        entry.id = entry_id
        self._entries[entry_id] = entry

        # Generate embedding if possible
        embedder = self._get_embedder()
        if embedder:
            embedding = embedder.encode([document], normalize_embeddings=True)
            self._embeddings[entry_id] = embedding[0]

        return entry_id

    def search(self, query: str, top_k: int = 3, **kwargs) -> List[HistorySearchResult]:
        if not self._entries:
            return []

        embedder = self._get_embedder()

        if embedder and self._embeddings:
            # Semantic search with embeddings
            query_emb = embedder.encode([query], normalize_embeddings=True)[0]
            scores = []
            for entry_id, entry_emb in self._embeddings.items():
                score = float(np.dot(query_emb, entry_emb))
                scores.append((entry_id, score))
            scores.sort(key=lambda x: x[1], reverse=True)

            results = []
            for entry_id, score in scores[:top_k]:
                if entry_id in self._entries:
                    results.append(HistorySearchResult(
                        entry=self._entries[entry_id],
                        score=score
                    ))
            return results
        else:
            # Fallback to keyword matching
            results = []
            query_lower = query.lower()
            for entry in self._entries.values():
                text = f"{entry.title} {entry.query_summary} {entry.solution}".lower()
                score = sum(1 for word in query_lower.split() if word in text) / max(len(query_lower.split()), 1)
                if score > 0:
                    results.append(HistorySearchResult(entry=entry, score=min(score, 1.0)))
            results.sort(key=lambda x: x.score, reverse=True)
            return results[:top_k]

    def get_entry(self, entry_id: str) -> Optional[HistoryEntry]:
        return self._entries.get(entry_id)

    def delete_entry(self, entry_id: str) -> bool:
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._embeddings.pop(entry_id, None)
            return True
        return False

    def count(self) -> int:
        return len(self._entries)

    def clear(self):
        self._entries.clear()
        self._embeddings.clear()


# Global instance
_history_rag = None


def get_history_rag():
    """Get or create the global HistoryRAG instance."""
    global _history_rag
    if _history_rag is None:
        if FAISS_AVAILABLE and SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                _history_rag = FAISSHistoryRAG()
            except Exception as e:
                logger.error(f"Failed to initialize FAISS RAG: {e}")
                _history_rag = InMemoryHistoryRAG()
        else:
            _history_rag = InMemoryHistoryRAG()
    return _history_rag


async def search_history(
    query: str,
    top_k: Optional[int] = None
) -> List[HistorySearchResult]:
    """Async wrapper for history search.

    Args:
        query: Search query
        top_k: Number of results

    Returns:
        List of search results
    """
    rag = get_history_rag()
    return rag.search(query, top_k)
