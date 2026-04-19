"""Tests for TRACKER_001 and TRACKER_002 compliance checks."""
import pytest

from src.analyzer.analyzer import ComplianceAnalyzer
from src.models.compliance import CheckCategory, CheckStatus, Severity
from src.models.scan import ExternalScript, PrivacyPolicyInfo, ScanResult


def _make_scan(
    policy_found: bool = True,
    policy_text: str | None = None,
    script_domains: list[str] | None = None,
) -> ScanResult:
    """Build a minimal ScanResult for tracker check tests."""
    scripts = [
        ExternalScript(url=f"https://{d}/script.js", page_url="https://example.com/", domain=d)
        for d in (script_domains or [])
    ]
    return ScanResult(
        url="https://example.com/",
        privacy_policy=PrivacyPolicyInfo(
            found=policy_found,
            text=policy_text,
            url="https://example.com/privacy" if policy_found else None,
        ),
        external_scripts=scripts,
    )


def _run_tracker_checks(scan: ScanResult) -> dict[str, object]:
    """Run only tracker checks; return {check_id: CheckItem}."""
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_trackers()
    return {item.id: item for item in analyzer.checklist}


# ── TRACKER_001 ───────────────────────────────────────────────────


def test_tracker_001_fail_not_mentioned_in_policy():
    """Tracker found in external_scripts but absent from policy text → FAIL."""
    scan = _make_scan(
        policy_text="Мы обрабатываем ваши данные в соответствии с законом.",
        script_domains=["mc.yandex.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.FAIL
    assert "Яндекс.Метрика" in checks["TRACKER_001"].details


def test_tracker_001_pass_tracker_mentioned_in_policy():
    """Tracker found and mentioned in policy text → PASS."""
    scan = _make_scan(
        policy_text="Мы используем яндекс.метрика для сбора статистики посещений.",
        script_domains=["mc.yandex.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.PASS


def test_tracker_001_pass_no_known_trackers():
    """No known trackers in external_scripts → PASS."""
    scan = _make_scan(
        policy_text="Стандартная политика без упоминания трекеров.",
        script_domains=["cdn.example.com", "static.mysite.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.PASS


def test_tracker_001_not_applicable_no_policy():
    """Policy not found → TRACKER_001 is NOT_APPLICABLE."""
    scan = _make_scan(
        policy_found=False,
        script_domains=["mc.yandex.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.NOT_APPLICABLE


def test_tracker_001_not_applicable_no_policy_text():
    """Policy found but text unavailable → TRACKER_001 is NOT_APPLICABLE."""
    scan = _make_scan(
        policy_found=True,
        policy_text=None,
        script_domains=["mc.yandex.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.NOT_APPLICABLE


def test_tracker_001_multiple_trackers_in_details():
    """Multiple undisclosed trackers — all listed in check details."""
    scan = _make_scan(
        policy_text="Политика обработки персональных данных.",
        script_domains=["mc.yandex.ru", "google-analytics.com"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.FAIL
    assert "Яндекс.Метрика" in checks["TRACKER_001"].details
    assert "Google Analytics" in checks["TRACKER_001"].details


def test_tracker_001_subdomain_matches():
    """Subdomain of a registry domain must be detected (e.g. embed.tawk.to → tawk.to)."""
    scan = _make_scan(
        policy_text="Политика не упоминает чат.",
        script_domains=["embed.tawk.to"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_001"].status == CheckStatus.FAIL
    assert "Tawk.to" in checks["TRACKER_001"].details


# ── TRACKER_002 ───────────────────────────────────────────────────


def test_tracker_002_fail_foreign_tracker_no_cross_border():
    """Foreign tracker found, no cross-border keywords in policy → FAIL."""
    scan = _make_scan(
        policy_text="Мы используем google analytics для аналитики сайта.",
        script_domains=["google-analytics.com"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_002"].status == CheckStatus.FAIL
    assert "Google Analytics" in checks["TRACKER_002"].details


def test_tracker_002_pass_cross_border_disclosed():
    """Foreign tracker found, policy mentions cross-border transfer → PASS."""
    scan = _make_scan(
        policy_text=(
            "Мы используем google analytics. "
            "Трансграничная передача данных осуществляется на основании ст. 12 152-ФЗ."
        ),
        script_domains=["google-analytics.com"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_002"].status == CheckStatus.PASS


def test_tracker_002_pass_cross_border_keyword_variants():
    """Each cross-border keyword variant triggers PASS."""
    keywords_texts = [
        "передача за рубеж осуществляется",
        "иностранным организациям передаются данные",
        "данные передаются в третьи страны",
        "хранение на зарубежных серверах",
    ]
    for text in keywords_texts:
        scan = _make_scan(
            policy_text=text,
            script_domains=["google-analytics.com"],
        )
        checks = _run_tracker_checks(scan)
        assert checks["TRACKER_002"].status == CheckStatus.PASS, (
            f"Expected PASS for text: '{text}'"
        )


def test_tracker_002_pass_only_russian_trackers():
    """Only Russian trackers (Яндекс.Метрика) → TRACKER_002 PASS (not foreign)."""
    scan = _make_scan(
        policy_text="Мы используем яндекс.метрика. Данные не передаются за рубеж.",
        script_domains=["mc.yandex.ru"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_002"].status == CheckStatus.PASS


def test_tracker_002_not_applicable_no_policy():
    """Policy not found → TRACKER_002 is NOT_APPLICABLE."""
    scan = _make_scan(
        policy_found=False,
        script_domains=["google-analytics.com"],
    )
    checks = _run_tracker_checks(scan)
    assert checks["TRACKER_002"].status == CheckStatus.NOT_APPLICABLE


def test_tracker_002_violation_severity_is_critical():
    """TRACKER_002 violation must have CRITICAL severity."""
    scan = _make_scan(
        policy_text="Мы используем google analytics.",
        script_domains=["google-analytics.com"],
    )
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_trackers()
    tracker_002_violations = [v for v in analyzer.violations if v.check_id == "TRACKER_002"]
    assert len(tracker_002_violations) == 1
    # TRACKER_002 = critical: трекер загружается до согласия = обработка ПДн без согласия (ч. 2 ст. 13.11)
    assert tracker_002_violations[0].severity == Severity.CRITICAL
    assert tracker_002_violations[0].category == CheckCategory.TRACKERS


def test_tracker_001_violation_severity_is_high():
    """TRACKER_001 violation must have HIGH severity."""
    scan = _make_scan(
        policy_text="Политика без упоминания трекеров.",
        script_domains=["mc.yandex.ru"],
    )
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_trackers()
    tracker_001_violations = [v for v in analyzer.violations if v.check_id == "TRACKER_001"]
    assert len(tracker_001_violations) == 1
    assert tracker_001_violations[0].severity == Severity.HIGH
    assert tracker_001_violations[0].category == CheckCategory.TRACKERS
