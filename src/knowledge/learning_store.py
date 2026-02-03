"""Learning Store - Storage and retrieval of learning data from CSM conversations."""

import hashlib
import pickle
import json
from typing import List, Optional, Dict, Any
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
from src.knowledge.history_updater import (
    LearningEntry,
    LearningPoints,
    QueryInterpretation,
    SearchHistory,
    ResponseEvolution,
    SearchIteration,
)


class LearningStore:
    """FAISS-based storage for learning data from CSM conversations.

    Stores complete learning entries and enables semantic search to find
    relevant learning points for similar queries.
    """

    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIM = 384

    def __init__(self, persist_dir: Optional[str] = None):
        """Initialize Learning Store.

        Args:
            persist_dir: Directory to persist data
        """
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS is not available. Install with: pip install faiss-cpu")

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise RuntimeError("sentence-transformers is not available.")

        base_dir = Path(persist_dir or settings.chroma_persist_dir)
        self.persist_dir = base_dir / "learning"
        self.index_path = self.persist_dir / "learning_faiss.index"
        self.metadata_path = self.persist_dir / "learning_metadata.pkl"

        # Load embedding model (reuse from history_rag if possible)
        logger.info(f"Loading embedding model for learning store: {self.EMBEDDING_MODEL}")
        self.embedder = SentenceTransformer(self.EMBEDDING_MODEL)

        # Load or create index
        self._load_or_create()

        logger.info(f"Learning Store initialized with {self.count()} entries")

    def _load_or_create(self):
        """Load existing index or create new one."""
        if self.index_path.exists() and self.metadata_path.exists():
            try:
                self.index = faiss.read_index(str(self.index_path))
                with open(self.metadata_path, 'rb') as f:
                    data = pickle.load(f)
                    self.entries: Dict[str, LearningEntry] = {}
                    # Convert dict back to LearningEntry objects
                    for k, v in data.get('entries', {}).items():
                        if isinstance(v, dict):
                            self.entries[k] = LearningEntry.from_dict(v)
                        else:
                            self.entries[k] = v
                    self.id_to_idx: Dict[str, int] = data.get('id_to_idx', {})
                logger.info(f"Loaded existing learning index with {self.index.ntotal} vectors")
            except Exception as e:
                logger.error(f"Failed to load existing learning index: {e}")
                self._create_new_index()
        else:
            self._create_new_index()

    def _create_new_index(self):
        """Create new empty index."""
        self.index = faiss.IndexFlatIP(self.EMBEDDING_DIM)
        self.entries: Dict[str, LearningEntry] = {}
        self.id_to_idx: Dict[str, int] = {}
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created new learning index")

    def _save(self):
        """Save index and metadata to disk."""
        try:
            faiss.write_index(self.index, str(self.index_path))
            # Convert LearningEntry to dict for serialization
            entries_dict = {k: v.to_dict() for k, v in self.entries.items()}
            with open(self.metadata_path, 'wb') as f:
                pickle.dump({
                    'entries': entries_dict,
                    'id_to_idx': self.id_to_idx
                }, f)
            logger.debug("Saved learning index and metadata")
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
        min_score: float = 0.3
    ) -> List[tuple[LearningEntry, float]]:
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

    def search(self, query: str, top_k: int = 5, min_score: float = 0.3) -> List[tuple[LearningEntry, float]]:
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

    Args:
        entry: LearningEntry to save

    Returns:
        Entry ID
    """
    store = get_learning_store()
    return store.add_entry(entry)


async def get_learning_for_query(query: str, top_k: int = 3) -> Dict[str, Any]:
    """Async wrapper for getting learning points.

    Args:
        query: Customer query
        top_k: Number of similar entries to consider

    Returns:
        Aggregated learning points
    """
    store = get_learning_store()
    return store.get_learning_points_for_query(query, top_k)
