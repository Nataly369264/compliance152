"""Tests for CONSENT_001–005 compliance checks (ст. 9 152-ФЗ)."""
import pytest

from src.analyzer.analyzer import ComplianceAnalyzer
from src.models.compliance import CheckCategory, CheckStatus
from src.models.scan import FormInfo, PrivacyPolicyInfo, ScanResult


def _make_scan(
    forms: list[FormInfo] | None = None,
    policy_text: str | None = None,
    policy_found: bool = True,
) -> ScanResult:
    """Build a minimal ScanResult for consent check tests."""
    return ScanResult(
        url="https://example.com/",
        forms=forms or [],
        privacy_policy=PrivacyPolicyInfo(
            found=policy_found,
            text=policy_text,
            url="https://example.com/privacy" if policy_found else None,
        ),
    )


def _pd_form(**kwargs) -> FormInfo:
    """Build a FormInfo that collects personal data, with optional overrides."""
    defaults = dict(
        page_url="https://example.com/",
        collects_personal_data=True,
        personal_data_fields=["email"],
        has_consent_checkbox=False,
        consent_checkbox_prechecked=False,
        consent_text=None,
        has_privacy_link=False,
        has_marketing_checkbox=False,
    )
    defaults.update(kwargs)
    return FormInfo(**defaults)


def _run_consent_checks(scan: ScanResult) -> dict[str, object]:
    """Run only consent checks; return {check_id: CheckItem}."""
    analyzer = ComplianceAnalyzer(scan, enable_web_verification=False)
    analyzer._check_consent()
    return {item.id: item for item in analyzer.checklist}


# ── CONSENT_001 ───────────────────────────────────────────────────


def test_consent_001_fail_no_checkbox_no_text():
    """Form without checkbox and without consent_text → FAIL."""
    scan = _make_scan(forms=[_pd_form()])
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_001"].status == CheckStatus.FAIL


def test_consent_001_pass_has_checkbox():
    """Form with consent checkbox → PASS."""
    scan = _make_scan(forms=[_pd_form(has_consent_checkbox=True)])
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_001"].status == CheckStatus.PASS


def test_consent_001_not_applicable_no_pd_forms():
    """No forms collecting personal data → NOT_APPLICABLE for all consent checks."""
    scan = _make_scan(forms=[])
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_001"].status == CheckStatus.NOT_APPLICABLE
    assert checks["CONSENT_002"].status == CheckStatus.NOT_APPLICABLE
    assert checks["CONSENT_003"].status == CheckStatus.NOT_APPLICABLE
    assert checks["CONSENT_004"].status == CheckStatus.NOT_APPLICABLE
    assert checks["CONSENT_005"].status == CheckStatus.NOT_APPLICABLE


# ── CONSENT_002 ───────────────────────────────────────────────────


def test_consent_002_fail_consent_in_oferta():
    """Policy text contains oferta markers + consent markers → FAIL."""
    policy_text = (
        "Настоящий документ является публичная оферта. "
        "Нажимая кнопку, вы даю согласие на обработку персональных данных."
    )
    scan = _make_scan(
        forms=[_pd_form(has_consent_checkbox=True)],
        policy_text=policy_text,
    )
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_002"].status == CheckStatus.FAIL


def test_consent_002_pass_no_oferta():
    """Policy text without oferta markers → PASS."""
    policy_text = "Политика конфиденциальности. Оператор обрабатывает ваши данные."
    scan = _make_scan(
        forms=[_pd_form(has_consent_checkbox=True)],
        policy_text=policy_text,
    )
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_002"].status == CheckStatus.PASS


# ── CONSENT_003 ───────────────────────────────────────────────────


def test_consent_003_fail_empty_consent_text():
    """No consent_text and empty policy → FAIL (missing required elements)."""
    scan = _make_scan(
        forms=[_pd_form(has_consent_checkbox=True, consent_text=None)],
        policy_text=None,
        policy_found=False,
    )
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_003"].status == CheckStatus.FAIL


def test_consent_003_pass_all_elements_present():
    """consent_text contains all 5 required elements → PASS."""
    consent_text = (
        "Оператор: ООО «Пример». "
        "Цели обработки: предоставление услуг. "
        "Перечень: фамилия, имя, email, телефон, адрес. "
        "Срок хранения: в течение 3 лет. "
        "Отзыв: отозвать согласие можно направив заявление."
    )
    scan = _make_scan(
        forms=[_pd_form(has_consent_checkbox=True, consent_text=consent_text)],
    )
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_003"].status == CheckStatus.PASS


# ── CONSENT_005 ───────────────────────────────────────────────────


def test_consent_005_fail_marketing_mixed_in_consent():
    """consent_text mentions рассылк but no separate marketing checkbox → FAIL."""
    consent_text = (
        "Я соглашаюсь на обработку ПДн и получение маркетинговой рассылки."
    )
    scan = _make_scan(
        forms=[_pd_form(
            has_consent_checkbox=True,
            consent_text=consent_text,
            has_marketing_checkbox=False,
        )],
    )
    checks = _run_consent_checks(scan)
    assert checks["CONSENT_005"].status == CheckStatus.FAIL
