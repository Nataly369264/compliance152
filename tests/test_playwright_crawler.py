"""Smoke-tests for PlaywrightCrawler._crawl() — no real browser required."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scanner.playwright_crawler import PlaywrightCrawler
from src.scanner.tracker_registry import find_trackers_in_scripts

# ── HTML fixtures ────────────────────────────────────────────────────────────

HTML_WITH_BANNER = """
<html><body>
  <div class="cookie-banner">
    <p>Мы используем cookies.</p>
    <button>Принять</button>
    <a href="/privacy">Политика</a>
  </div>
</body></html>
"""

HTML_WITHOUT_BANNER = """
<html><body><p>Clean page, no banner.</p></body></html>
"""


# ── Minimal Playwright page mock ─────────────────────────────────────────────

class _MockPage:
    """Playwright page mock that fires 'request' events during goto().

    Pass request_urls to simulate network requests made by the page.
    The handler registered via page.on('request', ...) will be called
    for each URL, exactly as Playwright does in real usage.
    """

    def __init__(self, html: str, request_urls: list[str] | None = None):
        self._html = html
        self._request_urls = request_urls or []
        self._handlers: dict = {}

    def on(self, event: str, handler) -> None:
        self._handlers[event] = handler

    async def goto(self, url: str, **kwargs) -> MagicMock:
        for req_url in self._request_urls:
            req = MagicMock()
            req.url = req_url
            if "request" in self._handlers:
                self._handlers["request"](req)
        return MagicMock(status=200)

    async def content(self) -> str:
        return self._html

    async def close(self) -> None:
        pass


def _make_context(page: _MockPage) -> MagicMock:
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.cookies = AsyncMock(return_value=[])
    return context


@pytest.fixture
def crawler() -> PlaywrightCrawler:
    return PlaywrightCrawler(max_pages=1, timeout=5, crawl_delay=0)


# ── Test 1: нет трекеров → analytics_before_consent == False ─────────────────

async def test_no_trackers_analytics_before_consent_false(crawler):
    """Cookie banner present, no tracker requests → analytics_before_consent is False."""
    page = _MockPage(
        html=HTML_WITH_BANNER,
        request_urls=[
            "https://example.com/style.css",
            "https://example.com/logo.png",
            "https://fonts.googleapis.com/css?family=Roboto",  # CDN, not a tracker
        ],
    )
    result = await crawler._crawl(_make_context(page), "https://example.com", "example.com")

    assert result.cookie_banner.found is True
    assert result.cookie_banner.analytics_before_consent is False


# ── Test 2: Google Analytics до согласия → analytics_before_consent == True ──

async def test_google_analytics_before_consent_sets_flag(crawler):
    """Cookie banner + google-analytics.com request → analytics_before_consent is True."""
    page = _MockPage(
        html=HTML_WITH_BANNER,
        request_urls=[
            "https://example.com/main.js",
            "https://www.google-analytics.com/analytics.js",
        ],
    )
    result = await crawler._crawl(_make_context(page), "https://example.com", "example.com")

    assert result.cookie_banner.found is True
    assert result.cookie_banner.analytics_before_consent is True


# ── Test 3: реестр корректно определяет известные трекерные домены ────────────

def test_tracker_registry_detects_known_domains():
    """find_trackers_in_scripts identifies all expected tracker domains."""
    domains = [
        "www.google-analytics.com",    # Google Analytics
        "mc.yandex.ru",                # Яндекс.Метрика
        "connect.facebook.net",        # Meta Pixel
        "example.com",                 # не трекер
        "cdn.mysite.ru",               # не трекер
    ]
    found = find_trackers_in_scripts(domains)
    found_names = {t["name"] for t in found}

    assert "Google Analytics" in found_names
    assert "Яндекс.Метрика" in found_names
    assert "Meta Pixel" in found_names
    # Убеждаемся, что собственные домены не попали в трекеры
    assert len(found) == 3


# ── Test 4: нет баннера → analytics_before_consent не меняется ───────────────

async def test_no_banner_tracker_requests_do_not_set_flag(crawler):
    """No cookie banner → analytics_before_consent stays False even with trackers."""
    page = _MockPage(
        html=HTML_WITHOUT_BANNER,
        request_urls=[
            "https://www.google-analytics.com/analytics.js",
            "https://mc.yandex.ru/metrika/tag.js",
        ],
    )
    result = await crawler._crawl(_make_context(page), "https://example.com", "example.com")

    assert result.cookie_banner.found is False
    assert result.cookie_banner.analytics_before_consent is False


# ── Test 5: Bug A regression — PDF policy links not skipped ──────────────────

HTML_WITH_PDF_POLICY_LINK = """
<html><body>
  <a href="https://example.com/wp-content/policy.pdf">Политика конфиденциальности</a>
</body></html>
"""


async def test_pdf_policy_link_not_skipped_by_playwright_crawler():
    """Regression Bug A: PDF policy link must not be filtered by should_skip() in _crawl().

    Before the fix, should_skip() filtered .pdf URLs unconditionally so the PDF
    policy URL was never visited. After the fix, it bypasses the filter.
    """
    visited_urls: list[str] = []

    class _TrackingPage:
        def __init__(self, html: str) -> None:
            self._html = html
            self._handlers: dict = {}

        def on(self, event: str, handler) -> None:
            self._handlers[event] = handler

        async def goto(self, url: str, **kwargs) -> MagicMock:
            visited_urls.append(url)
            return MagicMock(status=200)

        async def content(self) -> str:
            return self._html

        async def close(self) -> None:
            pass

    call_count = [0]
    main_page = _TrackingPage(HTML_WITH_PDF_POLICY_LINK)
    pdf_page = _TrackingPage("<html><body>PDF placeholder</body></html>")

    async def new_page_side_effect():
        call_count[0] += 1
        return main_page if call_count[0] == 1 else pdf_page

    context = MagicMock()
    context.new_page = AsyncMock(side_effect=new_page_side_effect)
    context.cookies = AsyncMock(return_value=[])

    pw_crawler = PlaywrightCrawler(max_pages=2, timeout=5, crawl_delay=0)
    with patch("src.scanner.playwright_crawler.asyncio.sleep", new=AsyncMock()):
        result = await pw_crawler._crawl(context, "https://example.com", "example.com")

    assert any("policy.pdf" in u for u in visited_urls), (
        "PDF policy URL was filtered by should_skip — Bug A regression"
    )
