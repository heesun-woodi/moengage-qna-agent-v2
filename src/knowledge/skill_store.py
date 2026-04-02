"""Skill store for managing agent skill MD files.

Loads skill prompts from GCS/local storage backend.
Falls back to embedded defaults on first run, then persists to storage.
"""

from typing import Dict, Optional

from src.storage import get_storage_backend, StorageBackend
from src.utils.logger import logger


class SkillStore:
    """Load/save skill MD files from GCS or local storage."""

    SKILL_DIR = "skills"
    SKILL_FILES = {
        "write_response": "write_response.md",
        "rerank": "rerank.md",
        "query_optimizer": "query_optimizer.md",
        "csm_agent": "csm_agent.md",
        "support_bot": "support_bot.md",
        "csm_conversational": "csm_conversational.md",
        "learning_extraction": "learning_extraction.md",
        "thread_analyzer": "thread_analyzer.md",
        "grounding_validation": "grounding_validation.md",
        "pdf_parser": "pdf_parser.md",
        "retrospective": "retrospective.md",
    }

    def __init__(self, storage: Optional[StorageBackend] = None):
        self.storage = storage or get_storage_backend()
        self.skills: Dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        """Load all skill MD files. Fallback to embedded defaults."""
        from src.knowledge._skill_defaults import EMBEDDED_DEFAULTS

        loaded_count = 0
        default_count = 0

        for key, filename in self.SKILL_FILES.items():
            path = f"{self.SKILL_DIR}/{filename}"
            try:
                content = self.storage.read_bytes(path)
                if content:
                    self.skills[key] = content.decode("utf-8")
                    loaded_count += 1
                    continue
            except Exception as e:
                logger.warning(f"[SKILL] Failed to load {path}: {e}")

            # Fallback to embedded default
            default = EMBEDDED_DEFAULTS.get(key, "")
            if default:
                self.skills[key] = default
                try:
                    self._save_skill(key)
                    logger.info(f"[SKILL] Initialized {filename} from embedded default")
                except Exception as e:
                    logger.warning(f"[SKILL] Failed to save default {filename}: {e}")
                default_count += 1
            else:
                self.skills[key] = ""
                logger.warning(f"[SKILL] No default found for {key}")

        logger.info(f"[SKILL] Loaded {loaded_count} from storage, {default_count} from defaults")

    def get_skill(self, key: str) -> str:
        """Get a skill prompt by key."""
        return self.skills.get(key, "")

    def update_skill(self, key: str, content: str):
        """Update skill content and persist."""
        self.skills[key] = content
        self._save_skill(key)
        logger.info(f"[SKILL] Updated {key}")

    def append_to_skill(self, key: str, section: str):
        """Append a section to a skill (used by retrospective for upgrades)."""
        current = self.skills.get(key, "")
        self.skills[key] = f"{current}\n\n{section}"
        self._save_skill(key)
        logger.info(f"[SKILL] Appended to {key}")

    def _save_skill(self, key: str):
        """Save a single skill to storage."""
        filename = self.SKILL_FILES.get(key)
        if not filename:
            return
        path = f"{self.SKILL_DIR}/{filename}"
        self.storage.write_bytes(path, self.skills[key].encode("utf-8"))

    def reload(self):
        """Reload all skills from storage."""
        self._load_all()


# Global singleton
_skill_store: Optional[SkillStore] = None


def get_skill_store() -> SkillStore:
    """Get or create the global SkillStore instance."""
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillStore()
    return _skill_store
