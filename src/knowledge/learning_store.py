"""Learning Store - Storage and retrieval of learning data from CSM conversations."""

import hashlib
import json
import tempfile
from typing import List, Optional, Dict, Any, Tuple
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

# Try to import sentence-transformers
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

from config.settings import settings
from src.utils.logger import logger
from src.storage import get_storage_backend, StorageBackend
from src.knowledge.history_updater import (
    LearningEntry,
    LearningPoints,
    QueryInterpretation,
    SearchHistory,
    ResponseEvolution,
    SearchIteration,
)
from src.knowledge.learning_api_client import get_learning_api_client, is_learning_api_configured


class LearningStore:
    """FAISS-based storage for learning data from CSM conversations.

    Stores complete learning entries and enables semantic search to find
    relevant learning points for similar queries.
    """

    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM = 384

    def __init__(self, persist_dir: Optional[str] = None, storage: Optional[StorageBackend] = None):
        """Initialize Learning Store.

        Args:
            persist_dir: Directory to persist data (for local storage)
            storage: Storage backend to use (if None, uses configured backend)
        """
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS is not available. Install with: pip install faiss-cpu")

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise RuntimeError("sentence-transformers is not available.")

        # Use provided storage or get configured backend
        self.storage = storage or get_storage_backend()

        # Path prefixes for storage
        self.index_path = "learning/learning_faiss.index"
        self.metadata_path = "learning/learning_metadata.json"

        # Legacy paths (for local storage migration)
        base_dir = Path(persist_dir or settings.chroma_persist_dir)
        self.persist_dir = base_dir / "learning"
        self._legacy_index_path = self.persist_dir / "learning_faiss.index"
        self._legacy_metadata_path = self.persist_dir / "learning_metadata.pkl"
        self._legacy_json_path = self.persist_dir / "learning_metadata.json"

        # Load embedding model (reuse from history_rag if possible)
        logger.info(f"Loading embedding model for learning store: {self.EMBEDDING_MODEL}")
        self.embedder = SentenceTransformer(self.EMBEDDING_MODEL)

        # Load or create index
        self._load_or_create()

        logger.info(f"Learning Store initialized with {self.count()} entries")

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
                        logger.info(f"Loaded learning index from storage with {self.index.ntotal} vectors")
                        return
            except Exception as e:
                logger.error(f"Failed to load learning from storage backend: {e}")

        # Try legacy local JSON format
        if self._legacy_json_path.exists() and self._legacy_index_path.exists():
            try:
                self.index = faiss.read_index(str(self._legacy_index_path))
                with open(self._legacy_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._parse_metadata(data)
                logger.info("Loaded from legacy JSON format, migrating to storage backend...")
                self._save()
                return
            except Exception as e:
                logger.error(f"Failed to load legacy learning JSON: {e}")

        # Try legacy pickle format
        if self._legacy_metadata_path.exists() and self._legacy_index_path.exists():
            if self._try_load_legacy():
                logger.info("Migrated learning store from legacy pickle format")
                self._save()
                return

        # Create new index if nothing found
        self._create_new_index()

    def _parse_metadata(self, data: dict):
        """Parse metadata dict and reconstruct LearningEntry objects."""
        self.entries: Dict[str, LearningEntry] = {}
        for k, v in data.get('entries', {}).items():
            if isinstance(v, dict):
                self.entries[k] = LearningEntry.from_dict(v)
            else:
                self.entries[k] = v
        self.id_to_idx: Dict[str, int] = data.get('id_to_idx', {})

    def _try_load_legacy(self) -> bool:
        """Try to load legacy pickle format for migration."""
        try:
            import pickle
            if self._legacy_metadata_path.exists() and self._legacy_index_path.exists():
                self.index = faiss.read_index(str(self._legacy_index_path))
                with open(self._legacy_metadata_path, 'rb') as f:
                    data = pickle.load(f)
                    self._parse_metadata(data)
                return True
        except Exception as e:
            logger.error(f"Failed to load legacy pickle: {e}")
        return False

    def _create_new_index(self):
        """Create new empty index."""
        self.index = faiss.IndexFlatIP(self.EMBEDDING_DIM)
        self.entries: Dict[str, LearningEntry] = {}
        self.id_to_idx: Dict[str, int] = {}
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created new learning index")

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

            # Convert LearningEntry to dict for JSON serialization
            entries_dict = {k: v.to_dict() for k, v in self.entries.items()}
            metadata = {
                'entries': entries_dict,
                'id_to_idx': self.id_to_idx
            }
            self.storage.write_json(self.metadata_path, metadata)

            logger.info(f"Saved learning index and metadata to storage ({len(self.entries)} entries)")
        except Exception as e:
            logger.error(f"Failed to save learning index: {e}")

    def _generate_id(self, content: str) -> str:
        """Generate unique ID from content hash."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _create_document(self, entry: LearningEntry) -> str:
        """Create searchable document from learning entry.

        Focuses on the original query and learning points for similarity search.
        """
        parts = [
            f"원본 문의: {entry.original_query}",
            f"카테고리: {entry.category}",
        ]

        if entry.query_interpretation.final:
            parts.append(f"해석: {entry.query_interpretation.final}")

        if entry.learning_points.query_lesson:
            parts.append(f"문의 해석 학습: {entry.learning_points.query_lesson}")

        if entry.learning_points.search_lesson:
            parts.append(f"검색 학습: {entry.learning_points.search_lesson}")

        if entry.learning_points.response_lesson:
            parts.append(f"답변 학습: {entry.learning_points.response_lesson}")

        return "\n".join(parts)

    def add_entry(self, entry: LearningEntry) -> str:
        """Add a learning entry.

        Args:
            entry: LearningEntry to add

        Returns:
            Entry ID
        """
        document = self._create_document(entry)
        entry_id = entry.id or self._generate_id(document)

        # Check for existing entry
        if entry_id in self.entries:
            logger.debug(f"Learning entry {entry_id} already exists, updating...")
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

        logger.info(f"Added learning entry: {entry.original_query[:50]}... (ID: {entry_id})")
        return entry_id

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.3  # Lowered from 0.5 to improve initial stage matching
    ) -> List[Tuple[LearningEntry, float]]:
        """Search for similar learning entries.

        Args:
            query: Search query (usually the original customer question)
            top_k: Number of results to return
            min_score: Minimum similarity score (0-1)

        Returns:
            List of (LearningEntry, score) tuples
        """
        if self.index.ntotal == 0:
            return []

        # Generate query embedding
        query_embedding = self.embedder.encode([query], normalize_embeddings=True)
        query_embedding = query_embedding.astype('float32')

        # Search
        search_k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(query_embedding, search_k)

        # Build reverse mapping
        idx_to_id = {v: k for k, v in self.id_to_idx.items()}

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue

            entry_id = idx_to_id.get(int(idx))
            if not entry_id or entry_id not in self.entries:
                continue

            if score < min_score:
                continue

            results.append((self.entries[entry_id], float(score)))

        logger.info(f"Learning Store: Found {len(results)} similar entries for '{query[:50]}...'")
        return results

    def get_learning_points_for_query(
        self,
        query: str,
        top_k: int = 3
    ) -> Dict[str, Any]:
        """Get aggregated learning points for a query.

        Args:
            query: The customer query
            top_k: Number of similar entries to consider

        Returns:
            Dictionary with aggregated learning points
        """
        similar_entries = self.search(query, top_k=top_k)

        if not similar_entries:
            return {
                "has_learning": False,
                "query_lessons": [],
                "search_lessons": [],
                "response_lessons": [],
                "similar_queries": [],
            }

        query_lessons = []
        search_lessons = []
        response_lessons = []
        similar_queries = []

        for entry, score in similar_entries:
            if entry.learning_points.query_lesson:
                query_lessons.append({
                    "lesson": entry.learning_points.query_lesson,
                    "score": score,
                    "original_query": entry.original_query[:100],
                })

            if entry.learning_points.search_lesson:
                search_lessons.append({
                    "lesson": entry.learning_points.search_lesson,
                    "score": score,
                    "original_query": entry.original_query[:100],
                })

            if entry.learning_points.response_lesson:
                response_lessons.append({
                    "lesson": entry.learning_points.response_lesson,
                    "score": score,
                    "original_query": entry.original_query[:100],
                })

            similar_queries.append({
                "query": entry.original_query[:200],
                "final_response": entry.response_evolution.final_response[:500] if entry.response_evolution.final_response else "",
                "score": score,
            })

        return {
            "has_learning": True,
            "query_lessons": query_lessons,
            "search_lessons": search_lessons,
            "response_lessons": response_lessons,
            "similar_queries": similar_queries,
        }

    def get_entry(self, entry_id: str) -> Optional[LearningEntry]:
        """Get an entry by ID."""
        return self.entries.get(entry_id)

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        if entry_id in self.entries:
            del self.entries[entry_id]
            if entry_id in self.id_to_idx:
                del self.id_to_idx[entry_id]
            self._save()
            logger.info(f"Deleted learning entry: {entry_id}")
            return True
        return False

    def count(self) -> int:
        """Get total number of entries."""
        return len(self.entries)

    def clear(self):
        """Clear all entries."""
        self._create_new_index()
        self._save()
        logger.info("Learning Store cleared")

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the learning store."""
        if not self.entries:
            return {
                "total_entries": 0,
                "entries_with_query_lesson": 0,
                "entries_with_search_lesson": 0,
                "entries_with_response_lesson": 0,
                "avg_iterations": 0,
                "categories": {},
            }

        entries_with_query = sum(
            1 for e in self.entries.values()
            if e.learning_points.query_lesson
        )
        entries_with_search = sum(
            1 for e in self.entries.values()
            if e.learning_points.search_lesson
        )
        entries_with_response = sum(
            1 for e in self.entries.values()
            if e.learning_points.response_lesson
        )
        total_iterations = sum(e.iteration_count for e in self.entries.values())

        # Category distribution
        categories: Dict[str, int] = {}
        for entry in self.entries.values():
            cat = entry.category or "Unknown"
            categories[cat] = categories.get(cat, 0) + 1

        return {
            "total_entries": len(self.entries),
            "entries_with_query_lesson": entries_with_query,
            "entries_with_search_lesson": entries_with_search,
            "entries_with_response_lesson": entries_with_response,
            "avg_iterations": total_iterations / len(self.entries) if self.entries else 0,
            "categories": categories,
        }


class InMemoryLearningStore:
    """In-memory fallback when FAISS is not available."""

    def __init__(self):
        self._entries: Dict[str, LearningEntry] = {}
        self._embeddings: Dict[str, np.ndarray] = {}
        self._embedder = None
        logger.warning("Using in-memory Learning Store (FAISS not available)")

    def _get_embedder(self):
        if self._embedder is None and SENTENCE_TRANSFORMERS_AVAILABLE:
            self._embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        return self._embedder

    def _generate_id(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def add_entry(self, entry: LearningEntry) -> str:
        document = f"{entry.original_query} {entry.category}"
        entry_id = entry.id or self._generate_id(document)
        entry.id = entry_id
        self._entries[entry_id] = entry

        embedder = self._get_embedder()
        if embedder:
            embedding = embedder.encode([document], normalize_embeddings=True)
            self._embeddings[entry_id] = embedding[0]

        return entry_id

    def search(self, query: str, top_k: int = 5, min_score: float = 0.3) -> List[Tuple[LearningEntry, float]]:
        if not self._entries:
            return []

        embedder = self._get_embedder()

        if embedder and self._embeddings:
            query_emb = embedder.encode([query], normalize_embeddings=True)[0]
            scores = []
            for entry_id, entry_emb in self._embeddings.items():
                score = float(np.dot(query_emb, entry_emb))
                if score >= min_score:
                    scores.append((entry_id, score))
            scores.sort(key=lambda x: x[1], reverse=True)

            return [
                (self._entries[entry_id], score)
                for entry_id, score in scores[:top_k]
                if entry_id in self._entries
            ]
        else:
            # Keyword fallback
            results = []
            query_lower = query.lower()
            for entry in self._entries.values():
                text = entry.original_query.lower()
                score = sum(1 for word in query_lower.split() if word in text) / max(len(query_lower.split()), 1)
                if score >= min_score:
                    results.append((entry, min(score, 1.0)))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]

    def get_learning_points_for_query(self, query: str, top_k: int = 3) -> Dict[str, Any]:
        similar_entries = self.search(query, top_k=top_k)

        if not similar_entries:
            return {
                "has_learning": False,
                "query_lessons": [],
                "search_lessons": [],
                "response_lessons": [],
                "similar_queries": [],
            }

        query_lessons = []
        search_lessons = []
        response_lessons = []
        similar_queries = []

        for entry, score in similar_entries:
            if entry.learning_points.query_lesson:
                query_lessons.append({
                    "lesson": entry.learning_points.query_lesson,
                    "score": score,
                })
            if entry.learning_points.search_lesson:
                search_lessons.append({
                    "lesson": entry.learning_points.search_lesson,
                    "score": score,
                })
            if entry.learning_points.response_lesson:
                response_lessons.append({
                    "lesson": entry.learning_points.response_lesson,
                    "score": score,
                })
            similar_queries.append({
                "query": entry.original_query[:200],
                "score": score,
            })

        return {
            "has_learning": True,
            "query_lessons": query_lessons,
            "search_lessons": search_lessons,
            "response_lessons": response_lessons,
            "similar_queries": similar_queries,
        }

    def get_entry(self, entry_id: str) -> Optional[LearningEntry]:
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

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "entries_with_query_lesson": 0,
            "entries_with_search_lesson": 0,
            "entries_with_response_lesson": 0,
            "avg_iterations": 0,
            "categories": {},
        }


# Global instance
_learning_store = None


def get_learning_store():
    """Get or create the global LearningStore instance."""
    global _learning_store
    if _learning_store is None:
        if FAISS_AVAILABLE and SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                _learning_store = LearningStore()
            except Exception as e:
                logger.error(f"Failed to initialize Learning Store: {e}")
                _learning_store = InMemoryLearningStore()
        else:
            _learning_store = InMemoryLearningStore()
    return _learning_store


async def save_learning_entry(entry: LearningEntry) -> str:
    """Async wrapper for saving a learning entry.

    Saves to both local FAISS store and Railway API if configured.

    Args:
        entry: LearningEntry to save

    Returns:
        Entry ID
    """
    store = get_learning_store()
    entry_id = store.add_entry(entry)
    logger.info(f"Saved learning entry to local store: {entry_id}")

    # Also save to Railway if configured
    if is_learning_api_configured():
        try:
            api_client = get_learning_api_client()
            if api_client:
                entry.id = entry_id  # Use same ID for consistency
                remote_id = await api_client.add_entry(entry.to_dict())
                if remote_id:
                    logger.info(f"Saved learning entry to Railway: {remote_id}")
                else:
                    logger.warning(f"Failed to save learning to Railway, local save successful: {entry_id}")
        except Exception as e:
            logger.error(f"Railway Learning API error (local save successful): {e}")

    return entry_id


async def get_learning_for_query(query: str, top_k: int = 3) -> Dict[str, Any]:
    """Get learning points from local store and optionally merge with Railway.

    Args:
        query: Customer query
        top_k: Number of similar entries to consider

    Returns:
        Aggregated learning points from local + remote sources
    """
    store = get_learning_store()
    local_result = store.get_learning_points_for_query(query, top_k)

    # Try to get remote learning data if configured
    if is_learning_api_configured():
        try:
            remote_result = await _fetch_remote_learning(query, top_k)
            if remote_result and remote_result.get("has_learning"):
                local_result = _merge_learning_results(local_result, remote_result)
                logger.info("[LEARNING] Merged local + remote learning data")
        except Exception as e:
            logger.warning(f"[LEARNING] Remote fetch failed, using local only: {e}")

    return local_result


async def _fetch_remote_learning(query: str, top_k: int) -> Optional[Dict[str, Any]]:
    """Fetch learning data from Railway API.

    Note: Railway API doesn't have semantic search, so we fetch recent entries
    and do client-side filtering based on keywords.
    """
    api_client = get_learning_api_client()
    if not api_client:
        return None

    try:
        # Fetch recent entries from Railway
        result = await api_client.list_entries(limit=50)
        if not result or not result.get("entries"):
            return None

        # Simple keyword matching for now
        # (Full semantic search would require Railway-side embedding)
        query_lower = query.lower()
        matched_entries = []

        for entry_dict in result.get("entries", []):
            entry_query = entry_dict.get("original_query", "").lower()
            # Simple overlap score
            query_words = set(query_lower.split())
            entry_words = set(entry_query.split())
            if not query_words:
                continue
            overlap = len(query_words & entry_words)
            if overlap >= 2:  # At least 2 words match
                matched_entries.append((entry_dict, overlap / len(query_words)))

        if not matched_entries:
            return {"has_learning": False}

        # Sort by score and take top_k
        matched_entries.sort(key=lambda x: x[1], reverse=True)
        matched_entries = matched_entries[:top_k]

        # Format as learning result
        return _format_remote_entries_as_learning(matched_entries)

    except Exception as e:
        logger.warning(f"Remote learning fetch error: {e}")
        return None


def _format_remote_entries_as_learning(entries: list) -> Dict[str, Any]:
    """Format remote entries as learning result dict."""
    if not entries:
        return {"has_learning": False}

    query_lessons = []
    search_lessons = []
    response_lessons = []
    similar_queries = []

    for entry_dict, score in entries:
        lp = entry_dict.get("learning_points", {})

        if lp.get("query_lesson"):
            query_lessons.append({
                "lesson": lp["query_lesson"],
                "score": score,
                "original_query": entry_dict.get("original_query", "")[:100],
            })
        if lp.get("search_lesson"):
            search_lessons.append({
                "lesson": lp["search_lesson"],
                "score": score,
            })
        if lp.get("response_lesson"):
            response_lessons.append({
                "lesson": lp["response_lesson"],
                "score": score,
            })

        similar_queries.append({
            "query": entry_dict.get("original_query", "")[:200],
            "final_response": entry_dict.get("response_evolution", {}).get("final_response", "")[:500],
            "score": score,
        })

    return {
        "has_learning": True,
        "query_lessons": query_lessons,
        "search_lessons": search_lessons,
        "response_lessons": response_lessons,
        "similar_queries": similar_queries,
        "source": "remote",
    }


def _merge_learning_results(local: Dict[str, Any], remote: Dict[str, Any]) -> Dict[str, Any]:
    """Merge local and remote learning results, deduplicating by lesson text."""
    if not local.get("has_learning") and not remote.get("has_learning"):
        return {"has_learning": False}

    # Combine and deduplicate
    seen_lessons: set = set()

    def dedupe_lessons(local_list: list, remote_list: list) -> list:
        result = []
        for item in local_list + remote_list:
            lesson_text = item.get("lesson", "")
            if lesson_text and lesson_text not in seen_lessons:
                seen_lessons.add(lesson_text)
                result.append(item)
        # Sort by score descending
        result.sort(key=lambda x: x.get("score", 0), reverse=True)
        return result[:3]  # Keep top 3

    return {
        "has_learning": True,
        "query_lessons": dedupe_lessons(
            local.get("query_lessons", []),
            remote.get("query_lessons", [])
        ),
        "search_lessons": dedupe_lessons(
            local.get("search_lessons", []),
            remote.get("search_lessons", [])
        ),
        "response_lessons": dedupe_lessons(
            local.get("response_lessons", []),
            remote.get("response_lessons", [])
        ),
        "similar_queries": (
            local.get("similar_queries", []) +
            remote.get("similar_queries", [])
        )[:5],
    }
