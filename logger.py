"""Structured logging setup for Telegram Channel Migrator.

Provides a pre-configured logger with both console and file output,
using structured formatting for easy parsing and debugging.

On Windows, forces UTF-8 encoding on the console stream to avoid
cp1252 UnicodeEncodeError with emoji characters.
"""

import io
import logging
import sys
from datetime import datetime
from pathlib import Path


class MigrationFormatter(logging.Formatter):
    """Custom formatter that produces structured, readable log lines."""

    LEVEL_ICONS = {
        "DEBUG": "[DBG]",
        "INFO": "[INF]",
        "WARNING": "[WRN]",
        "ERROR": "[ERR]",
        "CRITICAL": "[!!!]",
    }

    def format(self, record: logging.LogRecord) -> str:
        icon = self.LEVEL_ICONS.get(record.levelname, "")
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return (
            f"{timestamp} {icon} [{record.levelname:<8}] "
            f"{record.name}: {record.getMessage()}"
        )


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return (
            f"{timestamp} [{record.levelname:<8}] "
            f"{record.name}: {record.getMessage()}"
        )


def setup_logger(name: str = "migrator", level: str = "INFO") -> logging.Logger:
    """Configure and return the application logger.

    Args:
        name: Logger name.
        level: Log level string (DEBUG, INFO, WARNING, ERROR).

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    # Console handler — force UTF-8 on Windows to avoid cp1252 emoji crashes
    if sys.platform == "win32":
        # Wrap stdout in a UTF-8 writer with 'replace' fallback
        utf8_stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        console = logging.StreamHandler(utf8_stdout)
    else:
        console = logging.StreamHandler(sys.stdout)

    console.setFormatter(MigrationFormatter())
    logger.addHandler(console)

    # File handler with plain formatting (always UTF-8)
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(PlainFormatter())
    logger.addHandler(file_handler)

    logger.info("Logger initialized -- log file: %s", log_file)
    return logger
