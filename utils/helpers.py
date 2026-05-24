"""
Miscellaneous helper functions.
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def human_interval(seconds: int) -> str:
    """Convert seconds into a human-readable interval string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining = seconds % 60
        parts = [f"{minutes}m"]
        if remaining:
            parts.append(f"{remaining}s")
        return " ".join(parts)
    elif seconds < 86400:
        hours = seconds // 3600
        remaining = (seconds % 3600) // 60
        parts = [f"{hours}h"]
        if remaining:
            parts.append(f"{remaining}m")
        return " ".join(parts)
    else:
        days = seconds // 86400
        remaining = (seconds % 86400) // 3600
        parts = [f"{days}d"]
        if remaining:
            parts.append(f"{remaining}h")
        return " ".join(parts)


def parse_interval(text: str) -> Optional[int]:
    """
    Parse an interval string into seconds.

    Supported formats:
        - "30"           → 30 seconds
        - "30s"          → 30 seconds
        - "5m"           → 300 seconds
        - "2h"           → 7200 seconds
        - "1d"           → 86400 seconds
        - "1h30m"        → 5400 seconds
    """
    import re

    text = text.strip().lower()

    # Pure number → treat as seconds
    if text.isdigit():
        return int(text)

    total = 0
    pattern = re.compile(r"(\d+)\s*([smhd])")
    matches = pattern.findall(text)
    if not matches:
        return None

    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    for value, unit in matches:
        total += int(value) * multipliers[unit]

    return total if total > 0 else None


def truncate(text: str, max_len: int = 4000) -> str:
    """Truncate text to fit within Telegram message limits."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n\n… (truncated)"


def uptime_string(start_time: datetime) -> str:
    """Return a human-readable uptime string from a start datetime."""
    delta = datetime.utcnow() - start_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def cleanup_old_logs(db, retention_days: int = 7) -> None:
    """Remove logs older than *retention_days* from the database."""
    try:
        removed = db.cleanup_logs(retention_days)
        if removed:
            logger.info("Cleaned up %d old log entries", removed)
    except Exception as exc:
        logger.error("Failed to clean up logs: %s", exc)
