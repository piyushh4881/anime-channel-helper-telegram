"""Configuration loader for Movie Indexer Bot.

Reads settings from .env file in this directory and provides
typed access via a frozen dataclass. Uses bot token authentication
(not userbot) with Telethon's MTProto transport.
"""

import os
import sys
import logging
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this module's directory
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the Movie Indexer Bot."""

    # ── Telegram API (needed for Telethon MTProto transport layer) ──
    api_id: int = field(
        default_factory=lambda: int(os.getenv("API_ID", "0"))
    )
    api_hash: str = field(
        default_factory=lambda: os.getenv("API_HASH", "")
    )

    # ── Bot authentication ──
    bot_token: str = field(
        default_factory=lambda: os.getenv("BOT_TOKEN", "")
    )
    session_name: str = field(
        default_factory=lambda: os.getenv("SESSION_NAME", "movie_indexer_bot")
    )

    # ── Channel configuration ──
    channel_id: int = field(
        default_factory=lambda: int(os.getenv("CHANNEL_ID", "0"))
    )

    # ── Admin users (comma-separated Telegram user IDs) ──
    admin_users: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x.strip())
            for x in os.getenv("ADMIN_USERS", "").split(",")
            if x.strip()
        )
    )

    # ── Database ──
    database_path: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_PATH",
            str(Path(__file__).parent / "movie_indexer.db"),
        )
    )

    # ── Scanning behaviour ──
    scan_batch_size: int = field(
        default_factory=lambda: int(os.getenv("SCAN_BATCH_SIZE", "100"))
    )
    rate_limit_delay: float = field(
        default_factory=lambda: float(os.getenv("RATE_LIMIT_DELAY", "0.5"))
    )

    # ── Logging ──
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    # ── Helpers ──────────────────────────────────────────────────────

    def validate(self) -> None:
        """Validate critical configuration values."""
        errors: list[str] = []

        if not self.api_id:
            errors.append("API_ID is required (get from https://my.telegram.org)")
        if not self.api_hash:
            errors.append("API_HASH is required (get from https://my.telegram.org)")
        if not self.bot_token:
            errors.append("BOT_TOKEN is required (get from @BotFather)")
        if not self.channel_id:
            errors.append("CHANNEL_ID is required (e.g. -1001234567890)")

        if errors:
            raise ValueError(
                "Configuration errors:\n" + "\n".join(f"  ✗ {e}" for e in errors)
            )

    @property
    def channel_id_short(self) -> int:
        """Channel ID without the -100 prefix, used for t.me/c/ links."""
        cid = abs(self.channel_id)
        s = str(cid)
        if s.startswith("100") and len(s) > 10:
            return int(s[3:])
        return cid


def load_config() -> Config:
    """Load, validate, and return the configuration."""
    config = Config()
    config.validate()
    return config
