"""Tests for PDF extraction and analyzer NOT_APPLICABLE branch."""
import pytest

from src.analyzer.analyzer import ComplianceAnalyzer
from src.models.compliance import CheckCategory, CheckStatus
from src.models.scan import PrivacyPolicyInfo, ScanResult
from src.scanner.pdf_extractor import (
    extract_text_from_pdf,
    is_pdf_content_type,
    is_pdf_url,
)


# ── Helpers ───────────────────────────────────────────────────────

def _make_pdf_bytes(text: str) -> bytes:
    """Build a minimal machine-generated PDF containing the given text."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    # Split into lines to avoid overflow
    y = 750
    for line in text.split("\n"):
        c.drawString(50, y, line[:90])
        y -= 14
        if y < 50:
            c.showPage()
            y = 750
    c.save()
    return buf.getvalue()


def _make_scan_pdf_policy(
    policy_found: bool = True,
    policy_text: str | None = None,
) -> ScanResult:
    return ScanResult(
        url="https://example.com/",
        privacy_policy=PrivacyPolicyInfo(
            found=policy_found,
            url="https://example.com/privacy.pdf" if policy_found else None,
            text=policy_text,
            is_separate_page=True,
        ),
    )


# ── extract_text_from_pdf ─────────────────────────────────────────

def test_extract_text_normal_pdf():
    """Machine-generated PDF with text → returns extracted string."""
    # Use ASCII text: reportlab default font does not embed Cyrillic on Windows
    content = _make_pdf_bytes("Privacy Policy document\nINN 1234567890 OGRN 1234567890123")
    result = extract_text_from_pdf(content)
    assert result is not None
    assert "1234567890" in result


def test_extract_text_truncated_to_20000():
    """Extracted text is capped at 20 000 characters."""
    long_text = "А" * 500 + "\n"  # ~500 chars per line
    content = _make_pdf_bytes(long_text * 50)
    result = extract_text_from_pdf(content)
    assert result is not None
    assert len(result) <= 20000


def test_extract_text_empty_pdf_returns_none():
    """PDF with no text content → returns None."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.save()  # blank page, no text
    content = buf.getvalue()

    result = extract_text_from_pdf(content)
    assert result is None


def test_extract_text_corrupted_bytes_returns_none():
    """Random bytes that are not a valid PDF → returns None."""
    result = extract_text_from_pdf(b"not a pdf at all \x00\x01\x02")
    assert result is None


def test_extract_text_short_content_returns_none():
    """PDF with fewer than MIN_USABLE_TEXT_LEN chars → treated as unreadable."""
    content = _make_pdf_bytes("OK")  # very short text
    result = extract_text_from_pdf(content)
    assert result is None


# ── is_pdf_content_type ───────────────────────────────────────────

def test_is_pdf_content_type_true():
    assert is_pdf_content_type("application/pdf") is True
    assert is_pdf_content_type("application/pdf; charset=utf-8") is True
    assert is_pdf_content_type("APPLICATION/PDF") is True


def test_is_pdf_content_type_false():
    assert is_pdf_content_type("text/html; charset=utf-8") is False
    assert is_pdf_content_type("application/json") is False
    assert is_pdf_content_type("") is False


# ── is_pdf_url ────────────────────────────────────────────────────

def test_is_pdf_url_true():
    assert is_pdf_url("https://example.com/privacy.pdf") is True
    assert is_pdf_url("https://example.com/docs/Policy.PDF") is True


def test_is_pdf_url_false():
    assert is_pdf_url("https://example.com/privacy-policy") is False
    assert is_pdf_url("https://example.com/privacy.html") is False
    assert is_pdf_url("https://example.com/pdf-reader") is False


# ── analyzer: NOT_APPLICABLE when found=True, text=None ──────────

def test_analyzer_policy_found_no_text_content_checks_not_applicable():
    """found=True, text=None → POLICY_003..016 all NOT_APPLICABLE."""
    scan = _make_scan_pdf_policy(policy_found=True, policy_text=None)
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
        assert checks[cid].status == CheckStatus.NOT_APPLICABLE, (
            f"{cid} должен быть NOT_APPLICABLE, получен {checks[cid].status}"
        )


def test_analyzer_policy_found_no_text_no_violations():
    """found=True, text=None → no false POLICY violations generated."""
    scan = _make_scan_pdf_policy(policy_found=True, policy_text=None)
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_privacy_policy()

    policy_violations = [v for v in analyzer.violations if v.check_id.startswith("POLICY_")]
    assert len(policy_violations) == 0


def test_analyzer_policy_found_no_text_policy001_is_pass():
    """found=True, text=None → POLICY_001 still PASS (policy IS found)."""
    scan = _make_scan_pdf_policy(policy_found=True, policy_text=None)
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_privacy_policy()

    checks = {item.id: item for item in analyzer.checklist}
    assert checks["POLICY_001"].status == CheckStatus.PASS


def test_analyzer_scan_limitations_pdf_unreadable():
    """found=True, text=None → scan_limitations includes PDF warning."""
    scan = _make_scan_pdf_policy(policy_found=True, policy_text=None)
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    limitations = analyzer._build_scan_limitations()

    assert any("PDF" in note for note in limitations)


def test_analyzer_pdf_with_text_runs_normal_checks():
    """found=True, text present → content checks run normally (not NOT_APPLICABLE)."""
    policy_text = (
        "ООО Ромашка ИНН 1234567890. Цели обработки персональных данных: "
        "исполнение договора. Правовое основание: ст. 6 152-ФЗ."
    )
    scan = _make_scan_pdf_policy(policy_found=True, policy_text=policy_text)
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_privacy_policy()

    checks = {item.id: item for item in analyzer.checklist}
    # At least POLICY_003 (operator name) and POLICY_007 (purposes) should not be NOT_APPLICABLE
    assert checks["POLICY_003"].status != CheckStatus.NOT_APPLICABLE
    assert checks["POLICY_007"].status != CheckStatus.NOT_APPLICABLE
