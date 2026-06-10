"""AniList GraphQL API client for anime studio lookups.

Uses the free, unauthenticated AniList API to search for anime/movie
titles and retrieve the primary animation studio. Results are cached
both in-memory and in the database to minimise API calls.

AniList rate limit: ~90 requests/minute.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"

# Query to find the main production studio for an anime by title
SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    title {
      romaji
      english
      native
    }
    studios(isMain: true) {
      nodes {
        name
      }
    }
  }
}
"""

# Fallback query that also checks MANGA type (for anime movies not tagged ANIME)
SEARCH_MOVIE_QUERY = """
query ($search: String) {
  anime: Media(search: $search, type: ANIME) {
    title { romaji english }
    studios(isMain: true) { nodes { name } }
  }
  movie: Media(search: $search, type: ANIME, format: MOVIE) {
    title { romaji english }
    studios(isMain: true) { nodes { name } }
  }
}
"""


class AniListClient:
    """Async client for the AniList GraphQL API.

    Performs studio lookups by anime title with:
    - In-memory LRU cache (dict-based)
    - Rate limiting (0.7s between requests)
    - Graceful error handling with fallback to "Unknown Studio"
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: dict[str, str] = {}
        self._cache_studio_year: dict[str, tuple[str, Optional[int]]] = {}
        self._rate_limit_delay: float = 0.7  # ~85 req/min, under the 90 limit

    async def _ensure_session(self) -> None:
        """Lazily create the HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("AniList HTTP session closed")

    # ── Public API ───────────────────────────────────────────────────

    async def search_studio(self, title: str) -> str:
        """Look up the primary animation studio for a title.

        Parameters
        ----------
        title : str
            Movie/anime title (e.g. "Spirited Away").

        Returns
        -------
        str
            Studio name (e.g. "Studio Ghibli") or "Unknown Studio".
        """
        studio, _ = await self.search_studio_and_year(title)
        return studio

    async def search_studio_and_year(self, title: str) -> tuple[str, Optional[int]]:
        """Look up the primary animation studio and release year for a title."""
        cache_key = title.lower().strip()
        if cache_key in self._cache_studio_year:
            return self._cache_studio_year[cache_key]

        # Fallback to single studio cache if present
        if cache_key in self._cache:
            return self._cache[cache_key], None

        await self._ensure_session()

        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME) {
            startDate {
              year
            }
            studios(isMain: true) {
              nodes {
                name
              }
            }
          }
        }
        """

        try:
            payload = {
                "query": query,
                "variables": {"search": title},
            }

            async with self._session.post(ANILIST_URL, json=payload) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(
                        f"AniList rate-limited in search_studio_and_year, waiting {retry_after}s"
                    )
                    await asyncio.sleep(retry_after)
                    return await self.search_studio_and_year(title)

                if resp.status != 200:
                    logger.error(f"AniList HTTP {resp.status} in search_studio_and_year for '{title}'")
                    return "Unknown Studio", None

                data = await resp.json()

            media = data.get("data", {}).get("Media")
            if not media:
                logger.debug(f"No AniList match in search_studio_and_year for: '{title}'")
                res = ("Unknown Studio", None)
                self._cache_studio_year[cache_key] = res
                return res

            year = media.get("startDate", {}).get("year")
            studios = media.get("studios", {}).get("nodes", [])
            studio_name = "Unknown Studio"
            if studios:
                studio_name = studios[0]["name"]

            logger.info(f"AniList: '{title}' -> Studio: '{studio_name}', Year: {year}")
            res = (studio_name, year)
            self._cache_studio_year[cache_key] = res
            # Keep standard cache updated too
            self._cache[cache_key] = studio_name
            return res

        except Exception as exc:
            logger.error(f"AniList unexpected error in search_studio_and_year for '{title}': {exc}")
            return "Unknown Studio", None
        finally:
            await asyncio.sleep(self._rate_limit_delay)

    def set_cache(self, title: str, studio: str) -> None:
        """Pre-populate the cache (e.g. from database on startup)."""
        self._cache[title.lower().strip()] = studio

    def cache_size(self) -> int:
        """Return the number of cached entries."""
        return len(self._cache)

    # ── Private helpers ──────────────────────────────────────────────

    async def _query_anilist(self, title: str) -> str:
        """Execute the GraphQL query against AniList."""
        await self._ensure_session()

        try:
            payload = {
                "query": SEARCH_QUERY,
                "variables": {"search": title},
            }

            async with self._session.post(ANILIST_URL, json=payload) as resp:
                # Handle rate limiting
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(
                        f"AniList rate-limited, waiting {retry_after}s"
                    )
                    await asyncio.sleep(retry_after)
                    return await self._query_anilist(title)

                if resp.status != 200:
                    logger.error(f"AniList HTTP {resp.status} for '{title}'")
                    return "Unknown Studio"

                data = await resp.json()

            # Extract studio from response
            media = data.get("data", {}).get("Media")
            if not media:
                logger.debug(f"No AniList match for: '{title}'")
                return "Unknown Studio"

            studios = media.get("studios", {}).get("nodes", [])
            if studios:
                studio_name = studios[0]["name"]
                logger.info(f"AniList: '{title}' -> {studio_name}")
                return studio_name

            logger.debug(f"No studio data for: '{title}'")
            return "Unknown Studio"

        except asyncio.TimeoutError:
            logger.warning(f"AniList timeout for: '{title}'")
            return "Unknown Studio"
        except aiohttp.ClientError as exc:
            logger.error(f"AniList connection error for '{title}': {exc}")
            return "Unknown Studio"
        except Exception as exc:
            logger.error(f"AniList unexpected error for '{title}': {exc}")
            return "Unknown Studio"
        finally:
            # Rate-limit pause between requests
            await asyncio.sleep(self._rate_limit_delay)

    async def fetch_anime_metadata(self, title: str) -> Optional[dict]:
        """Fetch full metadata for an anime by title.

        Returns
        -------
        dict or None
            A dictionary containing title, type, status, episodes, duration,
            averageScore, genres, studios, description, coverImage.
        """
        await self._ensure_session()

        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME) {
            title {
              romaji
              english
              native
            }
            type
            status
            episodes
            duration
            averageScore
            genres
            studios {
              nodes {
                name
                isAnimationStudio
              }
            }
            description
            coverImage {
              extraLarge
              large
            }
          }
        }
        """

        try:
            payload = {
                "query": query,
                "variables": {"search": title},
            }

            async with self._session.post(ANILIST_URL, json=payload) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(f"AniList rate-limited in fetch_anime_metadata, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                    return await self.fetch_anime_metadata(title)

                if resp.status != 200:
                    logger.error(f"AniList HTTP {resp.status} in fetch_anime_metadata for '{title}'")
                    return None

                data = await resp.json()
                return data.get("data", {}).get("Media")
        except asyncio.TimeoutError:
            logger.warning(f"AniList timeout in fetch_anime_metadata for: '{title}'")
            return None
        except aiohttp.ClientError as exc:
            logger.error(f"AniList connection error in fetch_anime_metadata for '{title}': {exc}")
            return None
        except Exception as exc:
            logger.error(f"AniList unexpected error in fetch_anime_metadata for '{title}': {exc}")
            return None
        finally:
            await asyncio.sleep(self._rate_limit_delay)

    async def search_anime_info(self, title: str) -> dict:
        """Search AniList for anime details (Romaji, English, year, studio)."""
        cache_key = title.lower().strip()
        if not hasattr(self, "_cache_anime_info"):
            self._cache_anime_info = {}
            
        if cache_key in self._cache_anime_info:
            return self._cache_anime_info[cache_key]

        await self._ensure_session()

        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME) {
            title {
              romaji
              english
            }
            startDate {
              year
            }
            studios(isMain: true) {
              nodes {
                name
              }
            }
          }
        }
        """

        try:
            payload = {
                "query": query,
                "variables": {"search": title},
            }

            async with self._session.post(ANILIST_URL, json=payload) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    logger.warning(f"AniList rate-limited in search_anime_info, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                    return await self.search_anime_info(title)

                if resp.status != 200:
                    logger.error(f"AniList HTTP {resp.status} in search_anime_info for '{title}'")
                    return {}

                data = await resp.json()

            media = data.get("data", {}).get("Media")
            if not media:
                logger.debug(f"No AniList match in search_anime_info for: '{title}'")
                res = {}
                self._cache_anime_info[cache_key] = res
                return res

            romaji = media.get("title", {}).get("romaji")
            english = media.get("title", {}).get("english")
            year = media.get("startDate", {}).get("year")
            studios = media.get("studios", {}).get("nodes", [])
            studio_name = "Unknown Studio"
            if studios:
                studio_name = studios[0]["name"]

            res = {
                "romaji": romaji,
                "english": english,
                "year": year,
                "studio": studio_name,
            }
            self._cache_anime_info[cache_key] = res
            return res

        except Exception as exc:
            logger.error(f"AniList unexpected error in search_anime_info for '{title}': {exc}")
            return {}
        finally:
            await asyncio.sleep(self._rate_limit_delay)


