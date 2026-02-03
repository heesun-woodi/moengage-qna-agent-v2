"""Configuration settings for MoEngage Q&A Agent."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Slack Configuration (optional for testing)
    slack_bot_token: str = Field(default="", env="SLACK_BOT_TOKEN")
    slack_app_token: str = Field(default="", env="SLACK_APP_TOKEN")
    slack_signing_secret: str = Field(default="", env="SLACK_SIGNING_SECRET")

    # Anthropic Claude API (optional for testing)
    anthropic_api_key: str = Field(default="", env="ANTHROPIC_API_KEY")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379", env="REDIS_URL")

    # MoEngage API
    moengage_help_center_url: str = Field(
        default="https://help.moengage.com",
        env="MOENGAGE_HELP_CENTER_URL"
    )
    moengage_api_cache_ttl: int = Field(default=300, env="MOENGAGE_API_CACHE_TTL")

    # Search Settings
    moengage_search_top_k: int = Field(default=5, env="MOENGAGE_SEARCH_TOP_K")
    history_search_top_k: int = Field(default=3, env="HISTORY_SEARCH_TOP_K")

    # ChromaDB
    chroma_persist_dir: str = Field(default="./data/vectordb", env="CHROMA_PERSIST_DIR")

    # Emoji Configuration
    ticket_emoji: str = Field(default="ticket", env="TICKET_EMOJI")
    complete_emoji: str = Field(default="white_check_mark", env="COMPLETE_EMOJI")
    positive_feedback_emoji: str = Field(default="+1", env="POSITIVE_FEEDBACK_EMOJI")
    negative_feedback_emoji: str = Field(default="-1", env="NEGATIVE_FEEDBACK_EMOJI")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # History API (for remote PDF import to Railway)
    history_api_url: str = Field(default="", env="HISTORY_API_URL")
    history_api_key: str = Field(default="", env="HISTORY_API_KEY")
    history_api_enabled: bool = Field(default=False, env="HISTORY_API_ENABLED")
    history_api_port: int = Field(default=8080, env="HISTORY_API_PORT")

    # CSM Internal Channel Configuration
    csm_channel_ids: str = Field(default="", env="CSM_CHANNEL_IDS")
    csm_default_customer: str = Field(default="CSM Internal", env="CSM_DEFAULT_CUSTOMER")

    # CSM Response Channel - where bot posts answers for CSM review
    csm_response_channel_id: str = Field(default="", env="CSM_RESPONSE_CHANNEL_ID")

    def is_csm_channel(self, channel_id: str) -> bool:
        """Check if channel is a CSM internal channel."""
        if not self.csm_channel_ids:
            return False
        return channel_id in [cid.strip() for cid in self.csm_channel_ids.split(",") if cid.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
