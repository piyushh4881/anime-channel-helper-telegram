"""Caption cleanup processor for Telegram Channel Migrator.

Removes DDL (direct download link) references from message captions
while preserving all other Telegram formatting entities.

Handles:
  - MessageEntityTextUrl entities pointing to DDL URLs
  - Markdown-style links: [text](url)
  - HTML-style links: <a href="url">text</a>
  - Plain-text DDL URLs
  - Multiple DDL occurrences in a single caption
  - Cleanup of resulting empty lines and excess whitespace
"""

import re
import logging
from telethon.tl.types import (
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityStrike,
    MessageEntityUnderline,
    MessageEntitySpoiler,
    MessageEntityCustomEmoji,
)

logger = logging.getLogger("migrator.caption")

# Patterns that identify DDL content
DDL_PATTERNS = [
    re.compile(r"DDL", re.IGNORECASE),
]

# URL patterns for DDL links in plain text
DDL_URL_PATTERN = re.compile(
    r"https?://[^\s]*DDL[^\s]*", re.IGNORECASE
)

# Markdown link pattern: [text](url)
MARKDOWN_LINK_PATTERN = re.compile(
    r"\[([^\]]*DDL[^\]]*)\]\([^)]+\)", re.IGNORECASE
)

# Markdown link where URL contains DDL
MARKDOWN_URL_DDL_PATTERN = re.compile(
    r"\[[^\]]*\]\([^)]*DDL[^)]*\)", re.IGNORECASE
)

# HTML link pattern: <a href="...">...DDL...</a>
HTML_LINK_PATTERN = re.compile(
    r'<a\s+href="[^"]*">[^<]*DDL[^<]*</a>', re.IGNORECASE
)

# HTML link where href contains DDL
HTML_HREF_DDL_PATTERN = re.compile(
    r'<a\s+href="[^"]*DDL[^"]*">[^<]*</a>', re.IGNORECASE
)


def _clean_whitespace(text: str) -> str:
    """Remove excess blank lines and trailing spaces."""
    # Replace multiple consecutive newlines with a single newline
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove leading/trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]
    # Remove leading and trailing empty lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def clean_caption(text: str | None) -> str | None:
    """Remove all DDL references from caption text.

    Args:
        text: Original caption text, may be None.

    Returns:
        Cleaned caption text, or None if caption was None or becomes empty.
    """
    if not text:
        return text

    original = text

    # Remove HTML-style DDL links
    text = HTML_LINK_PATTERN.sub("", text)
    text = HTML_HREF_DDL_PATTERN.sub("", text)

    # Remove Markdown-style DDL links
    text = MARKDOWN_LINK_PATTERN.sub("", text)
    text = MARKDOWN_URL_DDL_PATTERN.sub("", text)

    # Remove plain-text DDL URLs
    text = DDL_URL_PATTERN.sub("", text)

    # Remove standalone DDL text occurrences
    for pattern in DDL_PATTERNS:
        text = pattern.sub("", text)

    # Clean up whitespace
    text = _clean_whitespace(text)

    if text != original:
        logger.debug("Caption cleaned: removed DDL references")

    return text if text else None


def clean_entities(
    text: str | None, entities: list | None
) -> tuple[str | None, list | None]:
    """Clean caption text and adjust formatting entities accordingly.

    This is the main entry point. It removes DDL references from both
    the text and associated MessageEntity objects (e.g., TextUrl entities
    whose URL or display text contains DDL).

    Args:
        text: Original message text/caption.
        entities: List of MessageEntity objects from the message.

    Returns:
        Tuple of (cleaned_text, cleaned_entities).
    """
    if not text:
        return text, entities

    # First, match "Uploaded by @HashHackers" (case-insensitive) and mark for removal
    removal_ranges: list[tuple[int, int]] = []  # (offset, length) to remove from text
    for match in re.finditer(r'[ \t]*Uploaded by @HashHackers[ \t]*', text, re.IGNORECASE):
        removal_ranges.append((match.start(), match.end() - match.start()))

    cleaned_entities = []

    if entities:
        for entity in entities:
            if isinstance(entity, MessageEntityTextUrl):
                # Check if the URL or the display text contains DDL
                url_has_ddl = "DDL" in (entity.url or "").upper()
                display_text = text[entity.offset : entity.offset + entity.length]
                text_has_ddl = "DDL" in display_text.upper()

                if url_has_ddl or text_has_ddl:
                    removal_ranges.append((entity.offset, entity.length))
                    logger.debug(
                        "Removing TextUrl entity: text=%r, url=%r",
                        display_text,
                        entity.url,
                    )
                    continue
            elif isinstance(entity, MessageEntityUrl):
                # Check if the URL text itself contains DDL
                url_text = text[entity.offset : entity.offset + entity.length]
                if "DDL" in url_text.upper():
                    removal_ranges.append((entity.offset, entity.length))
                    logger.debug("Removing URL entity: %r", url_text)
                    continue

            cleaned_entities.append(entity)

    # Remove marked ranges from text (process in reverse to preserve offsets)
    if removal_ranges:
        removal_ranges.sort(key=lambda r: r[0], reverse=True)
        for offset, length in removal_ranges:
            text = text[:offset] + text[offset + length :]

        # Adjust remaining entity offsets
        adjusted_entities = []
        removal_ranges.sort(key=lambda r: r[0])  # Sort forward for adjustment
        for entity in cleaned_entities:
            # Discard entity if it falls inside a removed range
            is_removed = False
            for r_offset, r_length in removal_ranges:
                if r_offset <= entity.offset < r_offset + r_length:
                    is_removed = True
                    break
            if is_removed:
                continue

            shift = 0
            for r_offset, r_length in removal_ranges:
                if r_offset < entity.offset:
                    shift += r_length
            entity.offset -= shift
            if entity.offset >= 0 and entity.offset < len(text):
                adjusted_entities.append(entity)
        cleaned_entities = adjusted_entities

    # Now clean remaining DDL text patterns
    text = clean_caption(text)

    if not text:
        return None, None

    return text, cleaned_entities if cleaned_entities else None
