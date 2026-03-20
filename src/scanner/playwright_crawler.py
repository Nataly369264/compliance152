"""Playwright-based crawler for JS-rendered sites (152-FZ compliance scanning).

Usage: only when USE_PLAYWRIGHT=true or use_playwright=True is passed explicitly.
Falls back to httpx+BS4 (SiteScanner) on any Playwright error.

Requires: playwright install chromium  (one-time setup)
"""
from __future__ import annotations

import logging

from src.models.scan import ScanResult

logger = logging.getLogger(__name__)


class PlaywrightCrawler:
    """Crawls JS-rendered websites via headless Chromium.

    Returns the same ScanResult format as SiteScanner (crawler.py),
    so it can be used as a drop-in replacement at the call site.

    Limits:
    - Max 20 pages (vs 50 for static mode — JS is slower)
    - 30 s timeout per page
    - On any Playwright error → falls back to SiteScanner and records
      the limitation in ScanResult.scan_limitations
    """

    def __init__(
        self,
        max_pages: int = 20,
        timeout: int = 30,
        crawl_delay: float = 1.0,
    ):
        self.max_pages = max_pages
        self.timeout = timeout
        self.crawl_delay = crawl_delay

    async def scan(self, url: str) -> ScanResult:
        """Scan a JS-rendered website and return structured ScanResult.

        Raises ImportError if playwright is not installed — caller should
        catch and fall back to SiteScanner.
        """
        raise NotImplementedError("PlaywrightCrawler.scan() — Этап 2 ещё не реализован")
