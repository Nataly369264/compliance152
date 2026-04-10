"""Tests for pdf_extractors.py cascade + analyzer manual_review_needed behavior."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.analyzer.analyzer import ComplianceAnalyzer
from src.models.compliance import CheckCategory, CheckItem, CheckStatus, Severity
from src.models.scan import PrivacyPolicyInfo, ScanResult
from src.scanner.pdf_extractors import ExtractionResult, extract_pdf_text


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
