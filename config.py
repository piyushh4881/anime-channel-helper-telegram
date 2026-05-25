import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration loaded from environment variables."""

    # Telegram Bot Token
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Owner / Admin Telegram user ID (numeric)
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))

    # Target channel(s) — comma-separated list of chat IDs or @usernames
    CHANNELS: list[str] = [
        ch.strip()
        for ch in os.getenv("CHANNELS", "").split(",")
        if ch.strip()
    ]

    # Database path
    # On Railway, set DATABASE_PATH=/data/bot.db (persistent volume)
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot_database.db")

    # Default scheduler interval in seconds
    DEFAULT_INTERVAL: int = int(os.getenv("DEFAULT_INTERVAL", "3600"))

    # Max retry attempts for failed sends before marking permanently failed
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

    # Logging level
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Auto-post mode (messages sent to bot PM are auto-queued)
    AUTO_POST_MODE: bool = os.getenv("AUTO_POST_MODE", "false").lower() == "true"

    # Parse mode: "HTML" or "Markdown"
    PARSE_MODE: str = os.getenv("PARSE_MODE", "HTML")

    # Log retention days
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "7"))

    # Number of items shown by /previewqueue
    PREVIEW_COUNT: int = int(os.getenv("PREVIEW_COUNT", "3"))

    @classmethod
    def validate(cls) -> None:
        """Validate that all required config values are set."""
        errors: list[str] = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is not set.")
        if cls.OWNER_ID == 0:
            errors.append("OWNER_ID is not set.")
        if not cls.CHANNELS:
            errors.append("CHANNELS is not set (provide at least one channel).")
        if errors:
            raise ValueError(
                "Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors)
            )
