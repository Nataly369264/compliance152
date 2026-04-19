"""Tests for SiteScanner → PlaywrightCrawler auto-fallback (DEC-002)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.server import _is_poor_result, _scan_with_fallback
from src.models.scan import PrivacyPolicyInfo, ScanResult


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_result(**kwargs) -> ScanResult:
    """Build a minimal ScanResult for testing."""
    defaults: dict = dict(
        url="https://example.com",
        pages_scanned=1,
        errors=[],
        privacy_policy=PrivacyPolicyInfo(found=True),
    )
    defaults.update(kwargs)
    return ScanResult(**defaults)


# ── _is_poor_result ───────────────────────────────────────────────────────────

def test_poor_result_pages_zero():
    result = _make_result(pages_scanned=0)
    assert _is_poor_result(result) is True


def test_poor_result_http_403_in_errors():
    result = _make_result(errors=["https://example.com: HTTP 403"])
    assert _is_poor_result(result) is True


def test_poor_result_http_429_in_errors():
    result = _make_result(errors=["https://example.com/page: HTTP 429"])
    assert _is_poor_result(result) is True


def test_poor_result_no_privacy_policy():
    result = _make_result(
        pages_scanned=2,
        privacy_policy=PrivacyPolicyInfo(found=False),
    )
    assert _is_poor_result(result) is True


def test_good_result_no_fallback_needed():
    result = _make_result(
        pages_scanned=5,
        errors=[],
        privacy_policy=PrivacyPolicyInfo(found=True),
    )
    assert _is_poor_result(result) is False


# ── _scan_with_fallback ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_called_when_pages_zero():
    """pages_scanned == 0 → PlaywrightCrawler must be invoked."""
    poor = _make_result(pages_scanned=0, privacy_policy=PrivacyPolicyInfo(found=False))
    good = _make_result(pages_scanned=3)

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=poor)
        MockPW.return_value.scan = AsyncMock(return_value=good)

        result = await _scan_with_fallback("https://example.com", 10)

    MockPW.return_value.scan.assert_called_once()
    assert result.pages_scanned == 3


@pytest.mark.asyncio
async def test_fallback_called_when_http_403():
    """HTTP 403 in errors[] → PlaywrightCrawler must be invoked."""
    poor = _make_result(
        pages_scanned=1,
        errors=["https://example.com/page: HTTP 403"],
    )
    good = _make_result(pages_scanned=4, errors=[])

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=poor)
        MockPW.return_value.scan = AsyncMock(return_value=good)

        result = await _scan_with_fallback("https://example.com", 10)

    MockPW.return_value.scan.assert_called_once()
    assert result.pages_scanned == 4


@pytest.mark.asyncio
async def test_fallback_called_when_no_privacy_policy():
    """privacy_policy.found == False при pages_scanned >= 1 → fallback."""
    poor = _make_result(
        pages_scanned=2,
        privacy_policy=PrivacyPolicyInfo(found=False),
    )
    good = _make_result(pages_scanned=2, privacy_policy=PrivacyPolicyInfo(found=True))

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=poor)
        MockPW.return_value.scan = AsyncMock(return_value=good)

        result = await _scan_with_fallback("https://example.com", 10)

    MockPW.return_value.scan.assert_called_once()
    assert result.privacy_policy.found is True


@pytest.mark.asyncio
async def test_no_fallback_on_good_result():
    """Хороший результат → PlaywrightCrawler не вызывается."""
    good = _make_result(
        pages_scanned=5,
        errors=[],
        privacy_policy=PrivacyPolicyInfo(found=True),
    )

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=good)
        MockPW.return_value.scan = AsyncMock()

        result = await _scan_with_fallback("https://example.com", 10)

    MockPW.return_value.scan.assert_not_called()
    assert result.pages_scanned == 5


@pytest.mark.asyncio
async def test_fallback_appends_scan_limitation():
    """После fallback в scan_limitations должна появиться диагностическая запись."""
    poor = _make_result(pages_scanned=0, privacy_policy=PrivacyPolicyInfo(found=False))
    good = _make_result(pages_scanned=3)

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=poor)
        MockPW.return_value.scan = AsyncMock(return_value=good)

        result = await _scan_with_fallback("https://example.com", 10)

    assert any("PlaywrightCrawler" in note for note in result.scan_limitations)


@pytest.mark.asyncio
async def test_playwright_called_with_capped_max_pages():
    """PlaywrightCrawler должен получить max_pages=min(request, 20)."""
    poor = _make_result(pages_scanned=0, privacy_policy=PrivacyPolicyInfo(found=False))
    good = _make_result(pages_scanned=1)

    with patch("src.api.server.SiteScanner") as MockSite, \
         patch("src.scanner.playwright_crawler.PlaywrightCrawler") as MockPW:
        MockSite.return_value.scan = AsyncMock(return_value=poor)
        MockPW.return_value.scan = AsyncMock(return_value=good)

        await _scan_with_fallback("https://example.com", 50)

    MockPW.assert_called_once_with(max_pages=20)
