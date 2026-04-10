"""Smoke-tests for SiteScanner (crawler.py) — no real HTTP requests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from src.models.scan import PrivacyPolicyInfo
from src.scanner.crawler import SiteScanner


# ── HTML fixtures ────────────────────────────────────────────────────────────

HTML_WITH_EMAIL_FORM = """
<html><body>
  <form action="/submit" method="post">
    <label for="em">Email</label>
    <input id="em" type="email" name="email" required>
    <input type="submit" value="OK">
  </form>
</body></html>
"""

HTML_WITHOUT_FORM = """
<html><body><p>No forms here.</p></body></html>
"""

HTML_FORM_WITH_CONSENT = """
<html><body>
  <form action="/register" method="post">
    <input type="text" name="name">
    <label for="cb">Согласие на обработку персональных данных</label>
    <input id="cb" type="checkbox" name="consent">
    <input type="submit" value="Send">
  </form>
</body></html>
"""

HTML_WITH_PRIVACY_LINK = """
<html><body>
  <p>Some content</p>
  <a href="/privacy-policy">Политика конфиденциальности</a>
</body></html>
"""

HTML_WITHOUT_PRIVACY_LINK = """
<html><body><p>No links here.</p></body></html>
"""

HTML_WITH_COOKIE_BANNER = """
<html><body>
  <div class="cookie-banner">
    <p>Мы используем cookies.</p>
    <button>Принять</button>
    <button>Отклонить</button>
    <a href="/privacy">Политика</a>
  </div>
</body></html>
"""

HTML_WITHOUT_COOKIE_BANNER = """
<html><body><p>Clean page, no banner.</p></body></html>
"""


# ── Helper: build a mock httpx.Response ─────────────────────────────────────

def _make_response(html: str, url: str = "https://example.com") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.text = html
    resp.content = html.encode()
    resp.cookies = {}
    resp.url = url
    return resp


def _make_ssl_response() -> MagicMock:
    """Minimal response for _check_ssl HEAD request."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    return resp


# ── Fixture: scanner with no crawl delay ────────────────────────────────────

@pytest.fixture
def scanner() -> SiteScanner:
    return SiteScanner(max_pages=1, timeout=5, crawl_delay=0)


# ── Group A: Form detection ──────────────────────────────────────────────────

async def test_form_with_email_detected(scanner):
    """Page with <input type='email'> → ScanResult.forms is not empty."""
    main_resp = _make_response(HTML_WITH_EMAIL_FORM)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert len(result.forms) > 0


async def test_page_without_form_has_empty_forms(scanner):
    """Page without forms → ScanResult.forms is empty."""
    main_resp = _make_response(HTML_WITHOUT_FORM)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert result.forms == []


async def test_form_with_consent_checkbox(scanner):
    """Form with consent checkbox → FormInfo.has_consent_checkbox == True."""
    main_resp = _make_response(HTML_FORM_WITH_CONSENT)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert len(result.forms) > 0
    assert result.forms[0].has_consent_checkbox is True


# ── Group B: Privacy policy detection ───────────────────────────────────────

async def test_privacy_policy_link_found(scanner):
    """Page with /privacy-policy link → ScanResult.privacy_policy is not None (found=True)."""
    # max_pages=1 → only the main page is crawled; privacy link is on it.
    # We also need to handle that /privacy-policy is queued but not fetched (max_pages=1).
    # The result should still find the link via is_privacy_policy_page on discovered URLs.
    main_resp = _make_response(HTML_WITH_PRIVACY_LINK)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    # privacy_policy is always a PrivacyPolicyInfo; check .found or .url
    assert result.privacy_policy is not None


async def test_privacy_policy_absent(scanner):
    """Page without privacy link → ScanResult.privacy_policy.found == False."""
    # max_pages=1, no fallback URLs match → found=False
    main_resp = _make_response(HTML_WITHOUT_PRIVACY_LINK)
    ssl_resp = _make_ssl_response()

    # Fallback GET requests also return 404
    not_found = MagicMock(spec=httpx.Response)
    not_found.status_code = 404
    not_found.headers = {"content-type": "text/html"}

    def side_effect(url, **kwargs):
        if url == "https://example.com":
            return main_resp
        return not_found

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(side_effect=side_effect)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert result.privacy_policy.found is False


def test_select_best_policy_prefers_longer_text():
    """_select_best_policy: two candidates → picks the one with longer text."""
    short = PrivacyPolicyInfo(
        found=True,
        url="https://example.com/privacy",
        text="Short policy text.",
    )
    long = PrivacyPolicyInfo(
        found=True,
        url="https://example.com/privacy-policy",
        text="A" * 5000,
    )
    result = SiteScanner._select_best_policy([short, long])
    assert result is long


# ── Group C: Cookie banner detection ────────────────────────────────────────

async def test_cookie_banner_detected(scanner):
    """HTML with <div class='cookie-banner'> → ScanResult.cookie_banner.found == True."""
    main_resp = _make_response(HTML_WITH_COOKIE_BANNER)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert result.cookie_banner.found is True


async def test_cookie_banner_absent(scanner):
    """HTML without banner → ScanResult.cookie_banner.found == False."""
    main_resp = _make_response(HTML_WITHOUT_COOKIE_BANNER)
    ssl_resp = _make_ssl_response()

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(return_value=main_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        result = await scanner.scan("https://example.com")

    assert result.cookie_banner.found is False


# ── Group D: Utility methods (synchronous, no mocking needed) ───────────────

def test_normalize_url_removes_fragment():
    url = "https://example.com/page#section"
    assert SiteScanner._normalize_url(url) == "https://example.com/page"


def test_normalize_url_removes_trailing_slash():
    url = "https://example.com/page/"
    assert SiteScanner._normalize_url(url) == "https://example.com/page"


def test_is_same_domain_true():
    assert SiteScanner._is_same_domain("https://example.com/page", "example.com") is True


def test_is_same_domain_www_stripped():
    assert SiteScanner._is_same_domain("https://www.example.com/page", "example.com") is True


def test_is_same_domain_false():
    assert SiteScanner._is_same_domain("https://other.com/page", "example.com") is False


def test_should_skip_jpg():
    assert SiteScanner._should_skip("https://example.com/image.jpg") is True


def test_should_skip_pdf():
    assert SiteScanner._should_skip("https://example.com/doc.pdf") is True


def test_should_skip_html_not_skipped():
    assert SiteScanner._should_skip("https://example.com/page") is False


# ── Group E: Bug A regression — PDF policy links ────────────────────────────

HTML_WITH_PDF_POLICY_LINK = """
<html><body>
  <a href="https://example.com/wp-content/policy.pdf">Политика конфиденциальности</a>
</body></html>
"""


async def test_pdf_policy_link_not_skipped_by_site_scanner():
    """Regression Bug A: PDF link with policy anchor text is not filtered by should_skip().

    Before the fix, should_skip() filtered .pdf URLs unconditionally so the PDF
    policy was never fetched. After the fix, privacy policy PDFs bypass the filter.
    """
    main_resp = _make_response(HTML_WITH_PDF_POLICY_LINK)
    ssl_resp = _make_ssl_response()

    pdf_resp = MagicMock(spec=httpx.Response)
    pdf_resp.status_code = 200
    pdf_resp.headers = {"content-type": "application/pdf"}
    pdf_resp.content = b"%PDF-1.4 fake"
    pdf_resp.cookies = {}

    def get_side_effect(url, **kwargs):
        if "policy.pdf" in url:
            return pdf_resp
        return main_resp

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=ssl_resp)
    mock_client.get = AsyncMock(side_effect=get_side_effect)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    # "многопользовательского" = 22 consecutive Cyrillic chars → is_russian=True
    valid_policy_text = "политика конфиденциальности многопользовательского сервиса " * 50

    scanner2 = SiteScanner(max_pages=2, timeout=5, crawl_delay=0)
    with patch("src.scanner.crawler.httpx.AsyncClient", return_value=mock_client):
        with patch("src.scanner.crawler.extract_text_from_pdf", return_value=valid_policy_text):
            result = await scanner2.scan("https://example.com")

    # Verify the PDF URL was actually fetched (not filtered by should_skip)
    fetched_urls = [str(call.args[0]) for call in mock_client.get.call_args_list]
    assert any("policy.pdf" in u for u in fetched_urls), (
        "PDF policy URL was filtered by should_skip — Bug A regression"
    )
    assert result.privacy_policy.found is True
    assert result.privacy_policy.url is not None
    assert "policy.pdf" in result.privacy_policy.url
