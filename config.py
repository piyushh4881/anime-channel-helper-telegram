"""Configuration loader for Telegram Channel Migrator.

Reads all settings from .env file and provides typed access
with sensible defaults for safe migration speeds.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_channel(value: str, allow_empty: bool = False) -> int | str | None:
    """Parse channel identifier — supports both integer IDs and usernames."""
    value = value.strip()
    if not value:
        if allow_empty:
            return None
        raise ValueError("Channel identifier cannot be empty")
    try:
        return int(value)
    except ValueError:
        return value.lstrip("@")


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the migration bot."""

    # Telegram API
    api_id: int = field(default_factory=lambda: int(os.getenv("API_ID", "0")))
    api_hash: str = field(default_factory=lambda: os.getenv("API_HASH", ""))
    session_name: str = field(
        default_factory=lambda: os.getenv("SESSION_NAME", "telegram_migrator")
    )

    # Channels
    source_channel: int | str = field(
        default_factory=lambda: _parse_channel(os.getenv("SOURCE_CHANNEL", ""))
    )
    destination_channel: int | str = field(
        default_factory=lambda: _parse_channel(os.getenv("DESTINATION_CHANNEL", ""))
    )
    log_channel: int | str | None = field(
        default_factory=lambda: _parse_channel(
            os.getenv("LOG_CHANNEL", ""), allow_empty=True
        )
    )

    # Delays (seconds)
    min_delay: float = field(
        default_factory=lambda: float(os.getenv("MIN_DELAY", "3"))
    )
    max_delay: float = field(
        default_factory=lambda: float(os.getenv("MAX_DELAY", "8"))
    )
    batch_delay: float = field(
        default_factory=lambda: float(os.getenv("BATCH_DELAY", "300"))
    )
    sub_batch_size: int = field(
        default_factory=lambda: int(os.getenv("SUB_BATCH_SIZE", "10"))
    )

    # Rate limits
    max_per_minute: int = field(
        default_factory=lambda: int(os.getenv("MAX_PER_MINUTE", "8"))
    )
    max_per_hour: int = field(
        default_factory=lambda: int(os.getenv("MAX_PER_HOUR", "200"))
    )
    max_per_day: int = field(
        default_factory=lambda: int(os.getenv("MAX_PER_DAY", "3000"))
    )

    # Cooldown
    cooldown_every: int = field(
        default_factory=lambda: int(os.getenv("COOLDOWN_EVERY", "100"))
    )
    cooldown_minutes: float = field(
        default_factory=lambda: float(os.getenv("COOLDOWN_MINUTES", "10"))
    )
    large_cooldown_every: int = field(
        default_factory=lambda: int(os.getenv("LARGE_COOLDOWN_EVERY", "1000"))
    )
    large_cooldown_minutes: float = field(
        default_factory=lambda: float(os.getenv("LARGE_COOLDOWN_MINUTES", "45"))
    )

    # Modes
    live_mode: bool = field(
        default_factory=lambda: os.getenv("LIVE_MODE", "false").lower() == "true"
    )
    dry_run: bool = field(
        default_factory=lambda: os.getenv("DRY_RUN", "false").lower() == "true"
    )
    batch_mode: bool = field(
        default_factory=lambda: os.getenv("BATCH_MODE", "true").lower() == "true"
    )
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("BATCH_SIZE", "100"))
    )
    session_limit: int = field(
        default_factory=lambda: int(os.getenv("SESSION_LIMIT", "0"))
    )
    session_limit_cooldown_minutes: float = field(
        default_factory=lambda: float(os.getenv("SESSION_LIMIT_COOLDOWN_MINUTES", "0"))
    )


    # Database
    database_path: str = field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "migration.db")
    )

    # Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    def validate(self) -> None:
        """Validate critical configuration values."""
        errors: list[str] = []
        if not self.api_id:
            errors.append("API_ID is required")
        if not self.api_hash:
            errors.append("API_HASH is required")
        if not self.source_channel:
            errors.append("SOURCE_CHANNEL is required")
        if not self.destination_channel:
            errors.append("DESTINATION_CHANNEL is required")
        if self.min_delay < 1:
            errors.append("MIN_DELAY should be >= 1 second for account safety")
        if self.max_delay < self.min_delay:
            errors.append("MAX_DELAY must be >= MIN_DELAY")
        if errors:
            raise ValueError(
                "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )


def load_config() -> Config:
    """Load and validate configuration."""
    config = Config()
    config.validate()
    return config
