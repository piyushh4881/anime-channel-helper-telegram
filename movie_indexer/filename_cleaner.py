"""Filename cleaner for .mkv movie files.

Extracts movie title, release year, and quality metadata from
filenames that contain resolution, codec, audio, and source tags.

Examples
--------
>>> clean_filename("Tekkonkinkreet (2006) [1080p] [DUAL-AUDIO] [x265] [10bit].mkv")
'Tekkonkinkreet (2006).mkv'

>>> extract_movie_info("Spirited Away (2001) [1080p] [BluRay] [Dual Audio].mkv")
('Spirited Away', 2001, '1080p BD')
"""

from __future__ import annotations

import re
from typing import Optional


# ── Tag patterns (case-insensitive) ──────────────────────────────────

RESOLUTION_RE = re.compile(
    r"(?:480|720|1080|2160|4320)[pi]", re.IGNORECASE
)

SOURCE_RE = re.compile(
    r"(?:Blu[\s\-]?Ray|BD(?:Rip)?|BR(?:Rip)?|WEB[\s\-]?DL|WEB[\s\-]?Rip|WEB"
    r"|HDRip|DVDRip|DVD|HDTV|SDTV|HDCam|CAM|TS|TC|REMUX)",
    re.IGNORECASE,
)

CODEC_RE = re.compile(
    r"(?:x264|x265|[hH]\.?264|[hH]\.?265|HEVC|AVC|AV1|XviD|DivX|VP9)",
    re.IGNORECASE,
)

AUDIO_RE = re.compile(
    r"(?:DUAL[\s\-]?AUDIO|MULTI[\s\-]?AUDIO|AAC(?:\s*\d\.\d)?"
    r"|DTS(?:[\s\-]?HD(?:[\s\-]?MA)?)?|FLAC|AC[\-]?3|E[\s\-]?AC[\-]?3"
    r"|Atmos|TrueHD|Opus|MP3|PCM|LPCM)",
    re.IGNORECASE,
)

BITDEPTH_RE = re.compile(r"(?:8|10|12)[\s\-]?bit", re.IGNORECASE)

HDR_RE = re.compile(
    r"(?:HDR10\+?|HDR|DV|Dolby[\s\-]?Vision|HLG|SDR)", re.IGNORECASE
)

MISC_RE = re.compile(
    r"(?:PROPER|REPACK|EXTENDED|UNCUT|UNRATED"
    r"|Director'?s?[\s\-]?Cut|Theatrical|IMAX|Open[\s\-]?Matte|Hybrid)",
    re.IGNORECASE,
)

# Matches any content inside square brackets
BRACKET_RE = re.compile(r"\[[^\]]*\]")

# Matches (YYYY) year pattern
YEAR_RE = re.compile(r"^(.+?)\s*\((\d{4})\)")

# Source tag normalisation map
_SOURCE_NORMALISE: dict[str, str] = {
    "BLURAY": "BD", "BLU-RAY": "BD", "BLU RAY": "BD",
    "BD": "BD", "BDRIP": "BD", "BRRIP": "BD", "BRIP": "BD",
    "WEBDL": "WEB-DL", "WEB-DL": "WEB-DL", "WEB DL": "WEB-DL",
    "WEBRIP": "WEBRip", "WEB-RIP": "WEBRip", "WEB RIP": "WEBRip",
    "WEB": "WEB",
    "REMUX": "REMUX",
    "HDTV": "HDTV", "SDTV": "SDTV",
    "DVDRIP": "DVDRip", "DVD": "DVD",
    "HDRIP": "HDRip", "HDCAM": "HDCam",
    "CAM": "CAM", "TS": "TS", "TC": "TC",
}


# ── Public API ───────────────────────────────────────────────────────

KNOWN_GROUPS = {
    "bonkai77", "rigav1", "rig", "izu", "arid", "judas", "animerg", 
    "anime time", "animekaizoku", "db", "kaizoku", "trix", "saon"
}


def strip_brackets_from_title(title: str) -> str:
    """Remove any text in brackets (square, curly, or non-year parentheses) in any form."""
    import re
    # Remove [...]
    title = re.sub(r"\[[^\]]*\]", "", title)
    # Remove {...}
    title = re.sub(r"\{[^\}]*\}", "", title)
    # Remove parenthesised non-year content (stuff) where stuff is not exactly 4 digits
    title = re.sub(r"\((?!\d{4}\))[^)]*\)", "", title)
    # Replace multiple spaces/underscores with a single space
    title = re.sub(r"[\s_]+", " ", title).strip()
    return title


def strip_release_groups(title: str) -> str:
    """Strip known release groups and mixed letter/digit prefix tags from the start of the title."""
    title_lower = title.lower()
    for group in KNOWN_GROUPS:
        if title_lower.startswith(group + " "):
            title = title[len(group) + 1:]
            title_lower = title_lower[len(group) + 1:]
        elif title_lower.startswith(group + "-"):
            title = title[len(group) + 1:]
            title_lower = title_lower[len(group) + 1:]
        elif title_lower.startswith(group + "_"):
            title = title[len(group) + 1:]
            title_lower = title_lower[len(group) + 1:]
            
    # Mix of letter and digit starting word, e.g., bonkai77, RigAV1
    first_word_match = re.match(r"^([a-zA-Z]*\d[a-zA-Z0-9]*)\b\s*", title)
    if first_word_match:
        word = first_word_match.group(1)
        if not (word.isdigit() and len(word) == 4):
            if any(c.isalpha() for c in word):
                title = title[first_word_match.end():]
            
    return title.strip()


def extract_movie_info(filename: str) -> tuple[str, Optional[int], str]:
    """Extract title, year, and quality string from a .mkv filename.

    Parameters
    ----------
    filename : str
        Original filename, e.g. ``"Tekkonkinkreet (2006) [1080p] [DUAL-AUDIO].mkv"``

    Returns
    -------
    tuple[str, int | None, str]
        ``(title, year, quality)`` — quality is like ``"1080p BD"`` or ``"Unknown"``.
    """
    # 1. Strip extension
    name = filename.rsplit(".", 1)[0] if "." in filename else filename

    # 2. Strip brackets first (to get rid of tags like [bonkai77] or [izu] immediately)
    name = strip_brackets_from_title(name)

    # 3. Strip starting group prefixes like bonkai77_ or RigAV1_ or Rig_
    name = re.sub(r"^[a-zA-Z]*\d[a-zA-Z0-9]*[\-_]+", "", name)
    name = re.sub(r"^Rig(?:AV\d+)?[\-_]+", "", name)

    # 4. Extract quality
    quality = extract_quality(name)

    # 5. Extract year (4-digit number between 1900 and 2100, excluding resolutions)
    year = None
    year_start = -1

    for match in re.finditer(r"\b(19\d{2}|20[0-3]\d)\b", name):
        val = int(match.group(1))
        if val not in (1080, 720, 2160, 480, 4320):
            year = val
            year_start = match.start()
            break

    if not year:
        # Try non-digit bounded year
        for match in re.finditer(r"(?<!\d)(19\d{2}|20[0-3]\d)(?!\d)", name):
            val = int(match.group(1))
            if val not in (1080, 720, 2160, 480, 4320):
                year = val
                year_start = match.start()
                break

    # 6. Extract title (everything before the year, or strip all tags if no year)
    if year_start != -1:
        title = name[:year_start].strip()
    else:
        # Fallback to stripping all tags
        title = _strip_all_tags(name)

    # 7. Clean underscores and dots from the title
    title = title.replace("_", " ").replace(".", " ")
    title = re.sub(r"\s+", " ", title).strip()

    # 8. Strip release groups
    title = strip_release_groups(title)

    # Final strip of dashes/spaces/parentheses
    title = title.strip(" -_.( )")

    if not title:
        title = filename.rsplit(".", 1)[0] if "." in filename else filename

    return title, year, quality


def extract_quality(name: str) -> str:
    """Extract quality descriptor from a filename.

    Returns something like ``"1080p BD"``, ``"720p WEB-DL"``, or ``"Unknown"``.
    """
    parts: list[str] = []

    # Resolution
    res_match = RESOLUTION_RE.search(name)
    if res_match:
        parts.append(res_match.group(0).lower().replace("i", "p"))

    # Source
    src_match = SOURCE_RE.search(name)
    if src_match:
        raw = src_match.group(0)
        key = raw.upper().replace(" ", "").replace("-", "")
        normalised = _SOURCE_NORMALISE.get(key, raw)
        parts.append(normalised)

    return " ".join(parts) if parts else "Unknown"


def clean_filename(filename: str) -> str:
    """Clean a .mkv filename to ``"Title (Year).mkv"``.

    Examples
    --------
    >>> clean_filename("Tekkonkinkreet (2006) [1080p] [DUAL-AUDIO] [x265].mkv")
    'Tekkonkinkreet (2006).mkv'
    >>> clean_filename("Princess Mononoke (1997) [720p][BD][x265].mkv")
    'Princess Mononoke (1997).mkv'
    """
    title, year, _ = extract_movie_info(filename)

    if year:
        return f"{title} ({year}).mkv"
    return f"{title}.mkv"


# ── Private helpers ──────────────────────────────────────────────────

def _strip_all_tags(name: str) -> str:
    """Remove all known tags when no year pattern is found."""
    result = name

    # Remove bracketed content  [...]
    result = BRACKET_RE.sub("", result)

    # Remove parenthesised non-year content  (stuff)
    result = re.sub(r"\((?!\d{4}\))[^)]*\)", "", result)

    # Remove each tag category
    for pattern in (RESOLUTION_RE, CODEC_RE, AUDIO_RE,
                    BITDEPTH_RE, SOURCE_RE, HDR_RE, MISC_RE):
        result = pattern.sub("", result)

    # Remove trailing release group  -GroupName
    result = re.sub(r"\-\w+$", "", result)

    # Dots → spaces, collapse whitespace
    result = result.replace(".", " ")
    result = re.sub(r"\s+", " ", result).strip()
    result = result.rstrip(" -_.")

    return result


def format_combined_title(romaji: str, english: Optional[str]) -> str:
    """Format combined title showing 'Romaji // English' if English title differs."""
    if not english:
        return romaji
    if romaji.lower().strip() == english.lower().strip():
        return romaji
    return f"{romaji} // {english}"

