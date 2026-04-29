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
    return PlaywrightCrawler(max_pages=1, timeout=5, crawl_delay=0, js_render_delay=0)


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

    pw_crawler = PlaywrightCrawler(max_pages=2, timeout=5, crawl_delay=0, js_render_delay=0)
    with patch("src.scanner.playwright_crawler.asyncio.sleep", new=AsyncMock()):
        await pw_crawler._crawl(context, "https://example.com", "example.com")

    assert any("policy.pdf" in u for u in visited_urls), (
        "PDF policy URL was filtered by should_skip — Bug A regression"
    )


# ── Test 6: CASE-010 regression — is_russian via _is_russian_text ─────────────

def test_is_russian_ocr_spaced_text(crawler):
    """Regression CASE-010: OCR text with spaces between Cyrillic letters gives is_russian=True.

    The old inline regex r'[а-яА-ЯёЁ]{20,}' required 20+ consecutive Cyrillic chars
    and returned False for OCR output like 'П о л и т и к а'.
    _is_russian_text counts Cyrillic share among all alpha chars — works correctly.
    """
    from bs4 import BeautifulSoup

    # Simulate OCR output: each Cyrillic letter separated by space
    ocr_text = (
        "П о л и т и к а к о н ф и д е н ц и а л ь н о с т и "
        "о р г а н и з а ц и и п о о б р а б о т к е "
        "п е р с о н а л ь н ы х д а н н ы х"
    )
    soup = BeautifulSoup(f"<html><body><p>{ocr_text}</p></body></html>", "lxml")
    result = crawler._extract_privacy_policy(soup, "https://example.com/policy", has_footer_link=False)

    assert result.is_russian is True, (
        "OCR-текст с пробелами между буквами должен давать is_russian=True"
    )


# ── Test 7: provenance fields populated ──────────────────────────────────────

def test_extract_privacy_policy_provenance_fields(crawler):
    """_extract_privacy_policy must return non-empty text_hash, fetched_at, content_length."""
    from bs4 import BeautifulSoup
    from datetime import datetime

    text = "Политика конфиденциальности. " * 10
    soup = BeautifulSoup(f"<html><body><p>{text}</p></body></html>", "lxml")
    result = crawler._extract_privacy_policy(soup, "https://example.com/policy", has_footer_link=False)

    assert result.text_hash is not None and len(result.text_hash) == 64, (
        "text_hash должен быть SHA-256 hex (64 символа)"
    )
    assert result.fetched_at is not None and isinstance(result.fetched_at, datetime), (
        "fetched_at должен быть объектом datetime"
    )
    assert result.content_length is not None and result.content_length > 0, (
        "content_length должен быть > 0"
    )


# ── Test 8: truncation at 100 000, not 20 000 ────────────────────────────────

def test_extract_privacy_policy_truncation_limit(crawler):
    """Text longer than 20 000 chars must not be truncated at 20 000 (limit is 100 000)."""
    from bs4 import BeautifulSoup

    # 25 000 Cyrillic chars — above old limit (20 000), below new limit (100 000)
    long_text = "а" * 25_000
    soup = BeautifulSoup(f"<html><body><p>{long_text}</p></body></html>", "lxml")
    result = crawler._extract_privacy_policy(soup, "https://example.com/policy", has_footer_link=False)

    assert len(result.text) > 20_000, (
        "Текст 25 000 символов не должен обрезаться на 20 000 — лимит теперь 100 000"
    )


# ── Stealth helpers ──────────────────────────────────────────────────────────

def _make_stealth_pw_mock():
    """Return (mock_async_playwright, mock_chromium, mock_context) for stealth tests."""
    mock_page = MagicMock()
    mock_page.on = MagicMock()
    mock_page.goto = AsyncMock(return_value=MagicMock(status=200, headers={}))
    mock_page.content = AsyncMock(return_value="<html><body></body></html>")
    mock_page.close = AsyncMock()

    mock_context = MagicMock()
    mock_context.add_init_script = AsyncMock()
    mock_context.cookies = AsyncMock(return_value=[])
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    mock_pw_cm = MagicMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw_cm)
    return mock_async_playwright, mock_chromium, mock_context


# ── Test 9: stealth — --disable-blink-features=AutomationControlled ──────────

async def test_stealth_launch_includes_automation_controlled_arg():
    """chromium.launch must pass --disable-blink-features=AutomationControlled in args."""
    mock_apw, mock_chromium, _ = _make_stealth_pw_mock()

    with patch("playwright.async_api.async_playwright", mock_apw):
        with patch("src.scanner.playwright_crawler.asyncio.sleep", new=AsyncMock()):
            crawler = PlaywrightCrawler(max_pages=1, timeout=5, crawl_delay=0, js_render_delay=0)
            await crawler.scan("https://example.com")

    mock_chromium.launch.assert_called_once()
    launch_args = mock_chromium.launch.call_args.kwargs.get("args", [])
    assert "--disable-blink-features=AutomationControlled" in launch_args, (
        "chromium.launch должен передавать --disable-blink-features=AutomationControlled"
    )


# ── Test 10: stealth — add_init_script patches navigator.webdriver ───────────

async def test_stealth_add_init_script_patches_navigator_webdriver():
    """context.add_init_script must be called with the navigator.webdriver patch."""
    mock_apw, _, mock_context = _make_stealth_pw_mock()

    with patch("playwright.async_api.async_playwright", mock_apw):
        with patch("src.scanner.playwright_crawler.asyncio.sleep", new=AsyncMock()):
            crawler = PlaywrightCrawler(max_pages=1, timeout=5, crawl_delay=0, js_render_delay=0)
            await crawler.scan("https://example.com")

    mock_context.add_init_script.assert_called_once_with(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
