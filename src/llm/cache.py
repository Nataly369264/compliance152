"""Cache for web context to avoid redundant searches.

Web context (legal verification results) is cached in memory with a TTL.
The cache key is (doc_type, date) — so the same doc type searched today
reuses the cached result. The general context has its own cache entry.

Cache is automatically cleared for expired entries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.config import WEB_CONTEXT_CACHE_TTL_HOURS

logger = logging.getLogger(__name__)


class WebContextCache:
    """In-memory TTL cache for web verification results."""

    def __init__(self, ttl_hours: int = WEB_CONTEXT_CACHE_TTL_HOURS):
        self.ttl = timedelta(hours=ttl_hours)
        # Key: (doc_type, date_str) → Value: (content, timestamp)
        self._cache: dict[tuple[str, str], tuple[str, datetime]] = {}

    def _make_key(self, doc_type: str) -> tuple[str, str]:
        """Create cache key from doc_type and current date."""
        return (doc_type, datetime.now().strftime("%Y-%m-%d"))

    def get(self, doc_type: str) -> str | None:
        """Get cached web context for a document type.

        Returns None if not cached or expired.
        """
        key = self._make_key(doc_type)
        entry = self._cache.get(key)

        if entry is None:
            return None

        content, timestamp = entry
        if datetime.now() - timestamp > self.ttl:
            # Expired — remove and return None
            del self._cache[key]
            logger.debug("Cache expired for %s", key)
            return None

        logger.debug("Cache hit for %s", key)
        return content

    def set(self, doc_type: str, content: str) -> None:
        """Store web context in cache."""
        key = self._make_key(doc_type)
        self._cache[key] = (content, datetime.now())
        logger.debug("Cached web context for %s (%d chars)", key, len(content))

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        logger.info("Web context cache cleared")

    def clear_expired(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        now = datetime.now()
        expired_keys = [
            k for k, (_, ts) in self._cache.items()
            if now - ts > self.ttl
        ]
        for k in expired_keys:
            del self._cache[k]
        if expired_keys:
            logger.debug("Cleared %d expired cache entries", len(expired_keys))
        return len(expired_keys)

    @property
    def size(self) -> int:
        """Number of entries in cache."""
        return len(self._cache)

    def stats(self) -> dict:
        """Return cache statistics."""
        self.clear_expired()
        return {
            "entries": self.size,
            "ttl_hours": self.ttl.total_seconds() / 3600,
            "keys": [f"{k[0]}:{k[1]}" for k in self._cache],
        }


# ── Module-level singleton ───────────────────────────────────────

_cache: WebContextCache | None = None


def get_cache() -> WebContextCache:
    """Get the global WebContextCache instance."""
    global _cache
    if _cache is None:
        _cache = WebContextCache()
    return _cache


async def get_web_context_cached(
    doc_type: str,
    doc_title: str = "",
    gather_fn=None,
) -> str:
    """Get web context with caching.

    If cached and not expired, returns cached value.
    Otherwise, calls gather_fn (defaults to gather_web_context) and caches the result.
    """
    cache = get_cache()

    # Check cache first
    cached = cache.get(doc_type)
    if cached is not None:
        logger.info("Using cached web context for %s", doc_type)
        return cached

    # Not cached — gather fresh context
    if gather_fn is None:
        from src.llm.verification import gather_web_context
        gather_fn = gather_web_context

    logger.info("Gathering fresh web context for %s", doc_type)
    context = await gather_fn(doc_type=doc_type, doc_title=doc_title)

    # Cache the result (even empty string, to avoid repeated failures)
    if context:
        cache.set(doc_type, context)

    return context
