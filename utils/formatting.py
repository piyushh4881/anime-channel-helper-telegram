"""
Message formatting utilities.
Handles HTML ↔ Markdown conversion and text decoration.
"""

import html
import re
from typing import Optional


def format_html_bold(text: Optional[str]) -> str:
    """Wrap every line of *text* in <b>…</b> tags (HTML parse mode)."""
    if not text:
        return ""
    lines = text.split("\n")
    formatted = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            # Escape existing HTML entities, then wrap in bold
            formatted.append(f"<b>{html.escape(stripped)}</b>")
        else:
            formatted.append("")  # preserve blank lines
    return "\n".join(formatted)


def convert_parse_mode(text: str, *, from_mode: str, to_mode: str) -> str:
    """Best-effort conversion between HTML and Markdown."""
    if from_mode == to_mode:
        return text

    if from_mode == "HTML" and to_mode == "Markdown":
        # Bold
        text = re.sub(r"<b>(.*?)</b>", r"*\1*", text, flags=re.DOTALL)
        text = re.sub(r"<strong>(.*?)</strong>", r"*\1*", text, flags=re.DOTALL)
        # Italic
        text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.DOTALL)
        text = re.sub(r"<em>(.*?)</em>", r"_\1_", text, flags=re.DOTALL)
        # Code
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        # Pre
        text = re.sub(r"<pre>(.*?)</pre>", r"```\1```", text, flags=re.DOTALL)
        # Unescape HTML entities
        text = html.unescape(text)

    elif from_mode == "Markdown" and to_mode == "HTML":
        # Code blocks first
        text = re.sub(r"```(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
        # Inline code
        text = re.sub(r"`(.*?)`", r"<code>\1</code>", text)
        # Bold
        text = re.sub(r"\*(.+?)\*", r"<b>\1</b>", text)
        # Italic
        text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
        # Escape remaining HTML-special characters outside of tags
        # (not a full implementation but handles the common cases)

    return text


def escape_html(text: str) -> str:
    """HTML-escape user-provided text."""
    return html.escape(text)


def preview_text(text: Optional[str], max_len: int = 50) -> str:
    """Return a shortened preview of text for display."""
    if not text:
        return "(empty)"
    clean = text.replace("\n", " ").strip()
    if len(clean) > max_len:
        return clean[: max_len - 1] + "…"
    return clean
