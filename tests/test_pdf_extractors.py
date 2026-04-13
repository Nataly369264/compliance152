"""Tests for pdf_extractors.py cascade + analyzer manual_review_needed behavior."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.analyzer.analyzer import ComplianceAnalyzer
from src.models.compliance import CheckCategory, CheckItem, CheckStatus, Severity
from src.models.scan import PrivacyPolicyInfo, ScanResult
from src.scanner.pdf_extractors import ExtractionResult, YandexVisionExtractor, extract_pdf_text


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_scan(found: bool = True, text: str | None = None) -> ScanResult:
    return ScanResult(
        url="https://example.com/",
        privacy_policy=PrivacyPolicyInfo(
            found=found,
            url="https://example.com/privacy.pdf" if found else None,
            text=text,
            is_separate_page=True,
        ),
    )


def _make_check(id_: str, status: CheckStatus) -> CheckItem:
    return CheckItem(
        id=id_,
        category=CheckCategory.PRIVACY_POLICY,
        title=id_,
        description="",
        status=status,
        severity=Severity.MEDIUM,
    )


# ── (a) Cascade: both extractors fail → method="failed" ──────────────────────

def test_cascade_all_extractors_fail_returns_failed():
    """PdfplumberExtractor returns None, YandexVisionExtractor returns None →
    extract_pdf_text returns ExtractionResult(text=None, method='failed')."""
    pdfplumber_result = ExtractionResult(text=None, method="pdfplumber", error="text_too_short")
    yandex_result = ExtractionResult(text=None, method="yandex_vision", error="not_implemented")

    with (
        patch("src.scanner.pdf_extractors.PdfplumberExtractor.extract",
              return_value=pdfplumber_result),
        patch("src.scanner.pdf_extractors.YandexVisionExtractor.extract",
              return_value=yandex_result),
    ):
        result = extract_pdf_text(b"%PDF-fake")

    assert result.text is None
    assert result.method == "failed"
    assert result.error == "all_extractors_failed"


# ── (b) Analyzer: unreadable PDF → MANUAL_REVIEW_NEEDED for content checks ──

def test_analyzer_unreadable_pdf_sets_manual_review_needed():
    """extract_pdf_text returned None → POLICY_003..016 all MANUAL_REVIEW_NEEDED."""
    scan = _make_scan(found=True, text=None)
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_privacy_policy()

    checks = {item.id: item for item in analyzer.checklist}
    content_ids = [
        "POLICY_003", "POLICY_004", "POLICY_005", "POLICY_006",
        "POLICY_007", "POLICY_008", "POLICY_009", "POLICY_010",
        "POLICY_011", "POLICY_012", "POLICY_013", "POLICY_014",
        "POLICY_015", "POLICY_016",
    ]
    for cid in content_ids:
        assert checks[cid].status == CheckStatus.MANUAL_REVIEW_NEEDED, (
            f"{cid} должен быть MANUAL_REVIEW_NEEDED, получен {checks[cid].status}"
        )


# ── (c) Score: MANUAL_REVIEW_NEEDED excluded from denominator ────────────────

def test_score_excludes_manual_review_needed_from_denominator():
    """Score = passed / (total - manual_review_needed), not passed / total.

    Setup: 5 PASS + 2 FAIL + 3 MANUAL_REVIEW_NEEDED = 10 checks.
    Expected: score = 5 / (5+2) = 71%, not 5/10 = 50%.
    """
    analyzer = ComplianceAnalyzer(_make_scan(found=False), enable_web_verification=False)
    analyzer.checklist = (
        [_make_check(f"P{i:03d}", CheckStatus.PASS) for i in range(5)]
        + [_make_check(f"F{i:03d}", CheckStatus.FAIL) for i in range(2)]
        + [_make_check(f"M{i:03d}", CheckStatus.MANUAL_REVIEW_NEEDED) for i in range(3)]
    )

    total = sum(
        1 for c in analyzer.checklist
        if c.status != CheckStatus.MANUAL_REVIEW_NEEDED
    )
    passed = sum(1 for c in analyzer.checklist if c.status == CheckStatus.PASS)
    score = int((passed / total) * 100) if total > 0 else 0

    assert total == 7, f"Ожидался знаменатель 7, получен {total}"
    assert score == 71, f"Ожидался score 71%, получен {score}%"


# ── YandexVisionExtractor ─────────────────────────────────────────────────────

_LONG_RUSSIAN_TEXT = "многопользовательского " * 30  # >50 chars, 20+ consecutive cyrillic


def _mock_resp(status_code: int, json_body: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


def _vision_ok_body(text: str) -> dict:
    return {"result": {"textAnnotation": {"fullText": text}}}


def _make_client_mock(responses: list) -> MagicMock:
    """Client mock that returns responses in sequence for each .post() call."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post = MagicMock(side_effect=responses)
    return client


def _make_page_mock() -> MagicMock:
    """Build a mock pdfplumber.Page that renders to empty PNG bytes."""
    page = MagicMock()
    img = MagicMock()
    img.save = MagicMock()  # writes nothing; BytesIO stays empty (b"")
    page.to_image.return_value = img
    return page


def _make_pdf_mock(pages: list) -> MagicMock:
    """Build a mock pdfplumber PDF context manager."""
    pdf_cm = MagicMock()
    pdf_cm.__enter__ = MagicMock(return_value=pdf_cm)
    pdf_cm.__exit__ = MagicMock(return_value=False)
    pdf_cm.pages = pages
    return pdf_cm


# (d) Happy path: Vision returns valid text → method="yandex_vision", text returned

def test_yandex_vision_happy_path(monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")

    client_mock = _make_client_mock([_mock_resp(200, _vision_ok_body(_LONG_RUSSIAN_TEXT))])
    pdf_mock = _make_pdf_mock([_make_page_mock()])
    with (
        patch("pdfplumber.open", return_value=pdf_mock),
        patch("src.scanner.pdf_extractors.httpx.Client", return_value=client_mock),
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is not None
    assert result.method == "yandex_vision"
    assert result.error is None
    assert "многопользовательского" in result.text


# (e) Network error on first attempt, success on second → retry works

def test_yandex_vision_retry_on_network_error(monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")

    import httpx as _httpx
    network_err = _httpx.TransportError("connection reset")
    success_resp = _mock_resp(200, _vision_ok_body(_LONG_RUSSIAN_TEXT))

    client_mock = _make_client_mock([network_err, success_resp])
    pdf_mock = _make_pdf_mock([_make_page_mock()])
    with (
        patch("pdfplumber.open", return_value=pdf_mock),
        patch("src.scanner.pdf_extractors.httpx.Client", return_value=client_mock),
        patch("src.scanner.pdf_extractors.time.sleep"),  # skip actual delay
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is not None
    assert result.method == "yandex_vision"
    assert client_mock.post.call_count == 2


# (e2) Network error on first two attempts, success on third → 3-attempt retry works

def test_yandex_vision_retry_network_3_attempts(monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")

    import httpx as _httpx
    network_err = _httpx.TransportError("connection reset")
    success_resp = _mock_resp(200, _vision_ok_body(_LONG_RUSSIAN_TEXT))

    client_mock = _make_client_mock([network_err, network_err, success_resp])
    pdf_mock = _make_pdf_mock([_make_page_mock()])
    with (
        patch("pdfplumber.open", return_value=pdf_mock),
        patch("src.scanner.pdf_extractors.httpx.Client", return_value=client_mock),
        patch("src.scanner.pdf_extractors.time.sleep"),
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is not None
    assert result.method == "yandex_vision"
    assert client_mock.post.call_count == 3  # two network errors + one success


# (f) 5xx on both attempts → return None with last error

def test_yandex_vision_5xx_both_attempts_returns_none(monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")

    client_mock = _make_client_mock([_mock_resp(503), _mock_resp(503)])
    pdf_mock = _make_pdf_mock([_make_page_mock()])
    with (
        patch("pdfplumber.open", return_value=pdf_mock),
        patch("src.scanner.pdf_extractors.httpx.Client", return_value=client_mock),
        patch("src.scanner.pdf_extractors.time.sleep"),
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is None
    assert result.method == "yandex_vision"
    assert "503" in result.error
    assert client_mock.post.call_count == 2


# (g) Vision returns empty text → error="empty_text", no retry

def test_yandex_vision_empty_text_returns_none(monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")

    client_mock = _make_client_mock([_mock_resp(200, _vision_ok_body(""))])
    pdf_mock = _make_pdf_mock([_make_page_mock()])
    with (
        patch("pdfplumber.open", return_value=pdf_mock),
        patch("src.scanner.pdf_extractors.httpx.Client", return_value=client_mock),
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is None
    assert result.error == "empty_text"
    assert client_mock.post.call_count == 1  # no retry on empty text


# (h) Missing credentials → immediate error, no HTTP call, no pdfplumber call

def test_yandex_vision_missing_credentials_no_http_call(monkeypatch):
    monkeypatch.delenv("YANDEX_VISION_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)

    with (
        patch("src.scanner.pdf_extractors.httpx.Client") as mock_cls,
        patch("pdfplumber.open") as mock_pdf,
    ):
        result = YandexVisionExtractor().extract(b"%PDF-fake")

    assert result.text is None
    assert result.error == "missing_credentials"
    mock_cls.assert_not_called()
    mock_pdf.assert_not_called()


# ── _is_russian ───────────────────────────────────────────────────────────────

from src.scanner.pdf_extractors import _is_russian  # noqa: E402


def test_is_russian_ocr_style_text():
    """OCR text with spaces between words must be recognised as Russian."""
    ocr_text = "политика обработки персональных данных " * 5  # 200+ Cyrillic chars, spaces
    assert _is_russian(ocr_text) is True


def test_is_russian_empty_string():
    assert _is_russian("") is False


def test_is_russian_latin_text():
    assert _is_russian("This is a plain English privacy policy text." * 5) is False


def test_is_russian_short_cyrillic_below_threshold():
    """Fewer than 50 Cyrillic characters must not pass even at 100% ratio."""
    assert _is_russian("привет мир") is False  # 9 Cyrillic chars


def test_is_russian_long_machine_text_still_passes():
    """Original machine-extracted text (long words, no spaces) must still pass."""
    assert _is_russian("многопользовательского " * 10) is True  # 200 Cyrillic chars
