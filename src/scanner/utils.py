"""Shared utilities for SiteScanner and PlaywrightCrawler."""
from __future__ import annotations

from urllib.parse import urlparse

SKIP_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".zip", ".rar", ".tar", ".gz",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv",
    ".woff", ".woff2", ".ttf", ".eot",
    ".css", ".js", ".json", ".xml",
})

FALLBACK_PRIVACY_PATHS: list[str] = [
    "/privacy-policy", "/privacy_policy", "/privacy",
    "/documents/privacy-policy", "/documents/privacy_policy",
    "/legal/privacy", "/legal/privacy-policy",
    "/info/privacy", "/pages/privacy-policy",
    "/politika-konfidencialnosti", "/obrabotka-personalnyh-dannyh",
    "/personal-data", "/personalnyye-dannyye",
]


def normalize_url(url: str) -> str:
    """Remove fragment and trailing slash from URL."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def is_same_domain(url: str, base_domain: str) -> bool:
    """Return True if url belongs to base_domain (www-insensitive)."""
    def _strip_www(domain: str) -> str:
        return domain[4:] if domain.startswith("www.") else domain

    netloc = urlparse(url).netloc.lower()
    return _strip_www(netloc) == _strip_www(base_domain)


def should_skip(url: str) -> bool:
    """Return True if the URL points to a non-HTML resource (image, PDF, etc.)."""
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in SKIP_EXTENSIONS)


_MIN_POLICY_TEXT_LENGTH = 500


def is_valid_policy_text(text: str | None, is_russian: bool) -> bool:
    """Return True if extracted text looks like a real privacy policy.

    Rejects WAF challenge pages and JS stubs that pass URL matching
    but contain no real policy content:
    - text shorter than 500 chars → likely a challenge/stub page
    - is_russian=False → garbled encoding or non-Russian stub

    URL-only candidates (text=None) are not validated here and pass through.
    """
    if text is None:
        return True
    return len(text) >= _MIN_POLICY_TEXT_LENGTH and is_russian
