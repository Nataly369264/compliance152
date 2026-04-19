from __future__ import annotations

import logging
import uuid
from datetime import datetime

from src.knowledge.loader import (
    estimate_fines,
    get_check_by_id,
    get_fine_by_id,
)
from src.llm.cache import get_web_context_cached
from src.llm.client import call_llm
from src.llm.prompts import (
    PRIVACY_POLICY_ANALYSIS_SYSTEM,
    PRIVACY_POLICY_ANALYSIS_USER,
    SCAN_ANALYSIS_SYSTEM,
    SCAN_ANALYSIS_USER,
)
from src.models.compliance import (
    CheckCategory,
    CheckItem,
    CheckStatus,
    ComplianceReport,
    FineEstimate,
    FineItem,
    ScanMetadata,
    Severity,
    Violation,
)
from src.scanner.tracker_registry import find_trackers_in_scripts

# Keywords indicating cross-border data transfer in a privacy policy
_CROSS_BORDER_KEYWORDS = [
    "трансграничн",
    "передача за рубеж",
    "иностранн",
    "третьи страны",
    "зарубежн",
]
from src.models.scan import ScanResult

logger = logging.getLogger(__name__)


class ComplianceAnalyzer:
    """Analyzes a ScanResult against the 152-FZ compliance checklist."""

    def __init__(self, scan_result: ScanResult, enable_web_verification: bool = True):
        self.scan = scan_result
        self.checklist: list[CheckItem] = []
        self.violations: list[Violation] = []
        self.enable_web_verification = enable_web_verification
        self._web_legal_context: str = ""

    async def analyze(self) -> ComplianceReport:
        """Run full compliance analysis.

        Before LLM analysis, gathers current legal context from the web
        and passes it to the LLM for more accurate assessment.
        """
        # Gather general legal context from the web for LLM analysis
        if self.enable_web_verification:
            try:
                from src.llm.verification import gather_general_legal_context
                self._web_legal_context = await get_web_context_cached(
                    doc_type="_general_analysis",
                    doc_title="Общий анализ соответствия 152-ФЗ",
                    gather_fn=lambda **_: gather_general_legal_context(),
                )
                if self._web_legal_context:
                    logger.info(
                        "Web legal context gathered for analysis: %d chars",
                        len(self._web_legal_context),
                    )
            except Exception as e:
                logger.warning("Web verification for analysis failed: %s", e)
                self._web_legal_context = ""

        self._check_forms()
        self._check_consent()
        self._check_cookies()
        self._check_privacy_policy()
        self._check_technical()
        self._check_trackers()
        self._check_regulatory()

        # LLM deep analysis of privacy policy text
        llm_analysis = None
        if self.scan.privacy_policy.found and self.scan.privacy_policy.text:
            llm_analysis = await self._analyze_policy_with_llm()

        # Calculate scores
        # MANUAL_REVIEW_NEEDED checks are excluded from the denominator — the
        # policy was found by URL but text is unreadable, so we cannot assess
        # content compliance and these checks must not penalise the score.
        total = sum(
            1 for c in self.checklist
            if c.status != CheckStatus.MANUAL_REVIEW_NEEDED
        )
        passed = sum(1 for c in self.checklist if c.status == CheckStatus.PASS)
        failed = sum(1 for c in self.checklist if c.status == CheckStatus.FAIL)
        warnings = sum(1 for c in self.checklist if c.status == CheckStatus.WARNING)

        score = int((passed / total) * 100) if total > 0 else 0
        risk_level = self._calculate_risk_level(score, self.violations)

        # Estimate fines
        violation_ids = [v.check_id for v in self.violations]
        fines_data = estimate_fines(violation_ids)
        fine_estimate = FineEstimate(
            min_total=fines_data["min_total"],
            max_total=fines_data["max_total"],
            breakdown=[FineItem(**item) for item in fines_data["breakdown"]],
        )

        # LLM summary
        summary = await self._generate_summary()

        pp = self.scan.privacy_policy
        scan_metadata = ScanMetadata(
            source_url=pp.url,
            fetched_at=pp.fetched_at,
            content_length=pp.content_length,
            text_hash=pp.text_hash,
            text_truncated=(
                pp.content_length > len(pp.text)
                if pp.content_length is not None and pp.text is not None
                else None
            ),
            extraction_method=pp.extraction_method,
        ) if pp.found else None

        return ComplianceReport(
            id=str(uuid.uuid4()),
            site_url=self.scan.url,
            scan_date=datetime.utcnow(),
            overall_score=score,
            risk_level=risk_level,
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            warnings=warnings,
            violations=self.violations,
            checklist=self.checklist,
            fine_estimate=fine_estimate,
            llm_analysis=llm_analysis,
            summary=summary,
            scan_limitations=self._build_scan_limitations(),
            scan_metadata=scan_metadata,
        )

    # ── Form checks ──────────────────────────────────────────────

    def _check_forms(self) -> None:
        pd_forms = [f for f in self.scan.forms if f.collects_personal_data]

        if not pd_forms:
            self._add_check("FORM_001", CheckCategory.FORMS, CheckStatus.NOT_APPLICABLE,
                            details="На сайте не обнаружены формы, собирающие персональные данные")
            for _cid in ("FORM_002", "FORM_003", "FORM_006", "FORM_007"):
                self._add_check(_cid, CheckCategory.FORMS, CheckStatus.NOT_APPLICABLE,
                                details="Не применимо: формы, собирающие ПДн, не обнаружены")
            return

        all_have_consent = all(f.has_consent_checkbox for f in pd_forms)
        self._add_check(
            "FORM_001", CheckCategory.FORMS,
            CheckStatus.PASS if all_have_consent else CheckStatus.FAIL,
            details=f"Форм с ПДн: {len(pd_forms)}, без согласия: "
                    f"{sum(1 for f in pd_forms if not f.has_consent_checkbox)}",
        )
        if not all_have_consent:
            for f in pd_forms:
                if not f.has_consent_checkbox:
                    self._add_violation(
                        "FORM_001", "Форма без согласия на обработку ПДн",
                        f"На странице {f.page_url} обнаружена форма, собирающая персональные данные "
                        f"({', '.join(f.personal_data_fields)}), но без чекбокса согласия.",
                        category=CheckCategory.FORMS, page_url=f.page_url,
                    )

        # FORM_002: consent checkbox pre-checked (separate violation from missing checkbox)
        prechecked = [f for f in pd_forms if f.has_consent_checkbox and f.consent_checkbox_prechecked]
        self._add_check(
            "FORM_002", CheckCategory.FORMS,
            CheckStatus.PASS if not prechecked else CheckStatus.FAIL,
            details=f"Форм с предотмеченным чекбоксом согласия: {len(prechecked)}",
        )
        if prechecked:
            for f in prechecked:
                self._add_violation(
                    "FORM_002", "Чекбокс согласия предотмечен по умолчанию",
                    f"На странице {f.page_url} чекбокс согласия установлен заранее: "
                    f"пользователь не выразил активного согласия на обработку ПДн.",
                    category=CheckCategory.FORMS, page_url=f.page_url,
                )

        # FORM_003: privacy link near form
        no_link = [f for f in pd_forms if not f.has_privacy_link]
        self._add_check(
            "FORM_003", CheckCategory.FORMS,
            CheckStatus.PASS if not no_link else CheckStatus.FAIL,
            details=f"Форм без ссылки на Политику: {len(no_link)}",
        )
        if no_link:
            self._add_violation(
                "FORM_003", "Нет ссылки на Политику обработки ПДн рядом с формой",
                f"{len(no_link)} форм(ы) не содержат ссылку на Политику.",
                category=CheckCategory.FORMS,
            )

        # FORM_006: separate marketing checkbox
        # This is a warning-level check since not all forms need it
        self._add_check(
            "FORM_006", CheckCategory.FORMS, CheckStatus.WARNING,
            details="Требуется ручная проверка наличия отдельного согласия для маркетинга",
        )

        # FORM_007: minimal data collection
        excessive = [f for f in pd_forms if len(f.personal_data_fields) > 5]
        self._add_check(
            "FORM_007", CheckCategory.FORMS,
            CheckStatus.WARNING if excessive else CheckStatus.PASS,
            details=f"Форм с >5 полями ПДн: {len(excessive)}",
        )

    # ── Consent checks ───────────────────────────────────────────

    def _check_consent(self) -> None:
        """Проверки согласия на обработку ПДн по ст. 9 152-ФЗ."""
        pd_forms = [f for f in self.scan.forms if f.collects_personal_data]

        if not pd_forms:
            for cid in ("CONSENT_001", "CONSENT_002", "CONSENT_003",
                        "CONSENT_004", "CONSENT_005"):
                self._add_check(cid, category=CheckCategory.CONSENT,
                                status=CheckStatus.NOT_APPLICABLE,
                                details="Формы сбора ПДн не обнаружены")
            return

        # CONSENT_001: наличие чекбокса/текста согласия
        forms_without_consent = [
            f for f in pd_forms
            if not f.has_consent_checkbox and not f.consent_text
        ]
        if forms_without_consent:
            self._add_check("CONSENT_001", category=CheckCategory.CONSENT,
                            status=CheckStatus.FAIL,
                            details=f"Формы без согласия: {len(forms_without_consent)}")
            self._add_violation(
                "CONSENT_001",
                title="Формы без механизма получения согласия",
                description=f"Обнаружено {len(forms_without_consent)} форм сбора ПДн без чекбокса или текста согласия",
                category=CheckCategory.CONSENT,
                page_url=self.scan.url,
            )
        else:
            self._add_check("CONSENT_001", category=CheckCategory.CONSENT,
                            status=CheckStatus.PASS,
                            details="Все формы содержат механизм согласия")

        # CONSENT_002: согласие не вшито в оферту
        consent_in_oferta = False
        if self.scan.privacy_policy and self.scan.privacy_policy.text:
            policy_lower = self.scan.privacy_policy.text.lower()
            oferta_markers = [
                "публичная оферта",
                "публичной оферт",
                "договор оферты",
                "договор-оферт",
                "условия оферты",
                "акцептом оферты",
                "акцепт оферты",
            ]
            consent_markers = [
                "даю согласие на обработку",
                "даю своё согласие",
                "соглашаюсь на обработку",
                "выражаю согласие",
                "даёт согласие на обработку",
                "дает согласие на обработку",
                "согласие на обработку персональных",
            ]
            has_oferta = any(m in policy_lower for m in oferta_markers)
            has_consent_in_text = any(m in policy_lower for m in consent_markers)
            if has_oferta and has_consent_in_text:
                consent_in_oferta = True

        if consent_in_oferta:
            self._add_check("CONSENT_002", category=CheckCategory.CONSENT,
                            status=CheckStatus.FAIL,
                            details="Согласие на ПДн включено в текст оферты")
            self._add_violation(
                "CONSENT_002",
                title="Согласие вшито в оферту",
                description="Согласие на обработку ПДн включено в текст публичной оферты. С 01.09.2025 согласие должно быть отдельным документом",
                category=CheckCategory.CONSENT,
                page_url=self.scan.url,
            )
        else:
            self._add_check("CONSENT_002", category=CheckCategory.CONSENT,
                            status=CheckStatus.PASS,
                            details="Согласие не обнаружено в тексте оферты")

        # CONSENT_003: обязательные реквизиты в тексте согласия
        all_consent_texts = " ".join(
            (f.consent_text or "") for f in pd_forms
        ).lower()
        if self.scan.privacy_policy and self.scan.privacy_policy.text:
            all_consent_texts += " " + self.scan.privacy_policy.text.lower()

        required_elements = {
            "оператор": ["оператор", "наименование оператора", "оператором является"],
            "цели": ["цел", "в целях", "для целей"],
            "перечень_данных": ["перечень", "фамилия", "имя", "email", "телефон", "адрес"],
            "сроки": ["срок", "хранени", "в течение", "до момента"],
            "отзыв": ["отзыв", "отозвать", "прекращени"],
        }
        missing = [
            element for element, markers in required_elements.items()
            if not any(m in all_consent_texts for m in markers)
        ]
        if missing:
            self._add_check("CONSENT_003", category=CheckCategory.CONSENT,
                            status=CheckStatus.FAIL,
                            details=f"Отсутствуют реквизиты: {', '.join(missing)}")
            self._add_violation(
                "CONSENT_003",
                title="Неполные реквизиты согласия",
                description=f"В тексте согласия отсутствуют обязательные элементы: {', '.join(missing)}",
                category=CheckCategory.CONSENT,
                page_url=self.scan.url,
            )
        else:
            self._add_check("CONSENT_003", category=CheckCategory.CONSENT,
                            status=CheckStatus.PASS,
                            details="Все обязательные реквизиты присутствуют")

        # CONSENT_004: возможность отзыва согласия
        withdrawal_markers = [
            "отзыв согласия", "отозвать согласие",
            "прекратить обработку", "отказаться от обработки",
            "направив заявление", "направить запрос",
        ]
        has_withdrawal_info = False
        if self.scan.privacy_policy and self.scan.privacy_policy.text:
            pp_lower = self.scan.privacy_policy.text.lower()
            has_withdrawal_info = any(m in pp_lower for m in withdrawal_markers)
        if not has_withdrawal_info:
            has_withdrawal_info = "отзыв" in all_consent_texts or "отозвать" in all_consent_texts

        if not has_withdrawal_info:
            self._add_check("CONSENT_004", category=CheckCategory.CONSENT,
                            status=CheckStatus.FAIL,
                            details="Не найдена информация о порядке отзыва согласия")
            self._add_violation(
                "CONSENT_004",
                title="Нет инструкции по отзыву согласия",
                description="На сайте не найдена информация о порядке отзыва согласия на обработку ПДн",
                category=CheckCategory.CONSENT,
                page_url=self.scan.url,
            )
        else:
            self._add_check("CONSENT_004", category=CheckCategory.CONSENT,
                            status=CheckStatus.PASS,
                            details="Информация об отзыве согласия найдена")

        # CONSENT_005: раздельные согласия для разных целей
        forms_with_consent = [f for f in pd_forms if f.has_consent_checkbox]
        mixed_consent = any(
            not f.has_marketing_checkbox and f.consent_text
            and any(m in f.consent_text.lower() for m in ["рассылк", "маркетинг", "реклам", "промо", "акци"])
            for f in forms_with_consent
        )
        if mixed_consent:
            self._add_check("CONSENT_005", category=CheckCategory.CONSENT,
                            status=CheckStatus.FAIL,
                            details="Маркетинговое согласие не отделено от основного")
            self._add_violation(
                "CONSENT_005",
                title="Смешанное согласие на обработку и маркетинг",
                description="Согласие на обработку ПДн и на маркетинговые рассылки объединены в одном чекбоксе. С 01.09.2025 требуются раздельные согласия",
                category=CheckCategory.CONSENT,
                page_url=self.scan.url,
            )
        else:
            self._add_check("CONSENT_005", category=CheckCategory.CONSENT,
                            status=CheckStatus.PASS,
                            details="Раздельные согласия или маркетинговые цели не обнаружены")

    # ── Cookie checks ────────────────────────────────────────────

    def _check_cookies(self) -> None:
        banner = self.scan.cookie_banner

        self._add_check(
            "COOKIE_001", CheckCategory.COOKIES,
            CheckStatus.PASS if banner.found else CheckStatus.FAIL,
            details="Cookie-баннер " + ("обнаружен" if banner.found else "не обнаружен"),
        )
        if not banner.found:
            self._add_violation(
                "COOKIE_001", "Отсутствует cookie-баннер",
                "На сайте не обнаружен механизм получения согласия на использование cookie.",
                category=CheckCategory.COOKIES, page_url=self.scan.url,
            )
            for _cid in ("COOKIE_002", "COOKIE_003", "COOKIE_005"):
                self._add_check(_cid, CheckCategory.COOKIES, CheckStatus.NOT_APPLICABLE,
                                details="Не применимо: cookie-баннер не обнаружен")
            return

        # COOKIE_002: decline button
        self._add_check(
            "COOKIE_002", CheckCategory.COOKIES,
            CheckStatus.PASS if banner.has_decline_button else CheckStatus.FAIL,
            details="Кнопка 'Отклонить' " + ("есть" if banner.has_decline_button else "нет"),
        )
        if not banner.has_decline_button:
            self._add_violation(
                "COOKIE_002", "Cookie-баннер без возможности отказа",
                "Cookie-баннер обнаружен, но не содержит равноценной кнопки отклонения. "
                "Пользователь лишён возможности отказаться от необязательных cookie.",
                category=CheckCategory.COOKIES, page_url=self.scan.url,
            )

        # COOKIE_003: categories off by default
        self._add_check(
            "COOKIE_003", CheckCategory.COOKIES,
            CheckStatus.PASS if banner.has_category_choice else CheckStatus.WARNING,
            details="Выбор категорий cookie: " + ("есть" if banner.has_category_choice else "нет"),
        )

        # COOKIE_005: analytics before consent
        if banner.analytics_before_consent:
            self._add_check(
                "COOKIE_005", CheckCategory.COOKIES, CheckStatus.FAIL,
                details="Аналитика загружается до получения согласия",
            )
            self._add_violation(
                "COOKIE_005", "Аналитика загружается до согласия на cookie",
                "Скрипты аналитики загружаются до получения согласия пользователя.",
                category=CheckCategory.COOKIES, page_url=self.scan.url,
            )
        else:
            self._add_check("COOKIE_005", CheckCategory.COOKIES, CheckStatus.PASS)

    # ── Privacy Policy checks ────────────────────────────────────

    def _check_privacy_policy(self) -> None:
        pp = self.scan.privacy_policy

        self._add_check(
            "POLICY_001", CheckCategory.PRIVACY_POLICY,
            CheckStatus.PASS if pp.found else CheckStatus.FAIL,
            details="Политика обработки ПДн " + ("найдена" if pp.found else "не найдена"),
        )
        if not pp.found:
            self._add_violation(
                "POLICY_001", "Политика обработки ПДн не опубликована",
                "На сайте не обнаружена Политика обработки персональных данных.",
                category=CheckCategory.PRIVACY_POLICY, page_url=self.scan.url,
            )
            _SKIPPED_POLICY = [
                "POLICY_002", "POLICY_003", "POLICY_004", "POLICY_005",
                "POLICY_006", "POLICY_007", "POLICY_008", "POLICY_009",
                "POLICY_010", "POLICY_011", "POLICY_012", "POLICY_013",
                "POLICY_014", "POLICY_015", "POLICY_016", "POLICY_017",
            ]
            for _cid in _SKIPPED_POLICY:
                self._add_check(_cid, CheckCategory.PRIVACY_POLICY, CheckStatus.NOT_APPLICABLE,
                                details="Не применимо: политика обработки ПДн не найдена на сайте")
            return

        # POLICY_002: link in footer of every page
        pages_without = [p for p in self.scan.pages if not p.has_privacy_link_in_footer]
        all_have = len(pages_without) == 0
        self._add_check(
            "POLICY_002", CheckCategory.PRIVACY_POLICY,
            CheckStatus.PASS if all_have else CheckStatus.FAIL,
            details=f"Страниц без ссылки на Политику в футере: {len(pages_without)}/{len(self.scan.pages)}",
        )
        if not all_have:
            self._add_violation(
                "POLICY_002", "Ссылка на Политику отсутствует в футере",
                f"{len(pages_without)} из {len(self.scan.pages)} страниц не имеют ссылки на Политику в футере.",
                category=CheckCategory.PRIVACY_POLICY,
            )

        # PDF / unreadable policy: found=True but text unavailable → manual_review_needed
        # for content checks. These are excluded from the score denominator so the
        # site is not penalised for a technically valid (but unreadable) policy.
        if not pp.text:
            _CONTENT_CHECKS = [
                "POLICY_003", "POLICY_004", "POLICY_005", "POLICY_006",
                "POLICY_007", "POLICY_008", "POLICY_009", "POLICY_010",
                "POLICY_011", "POLICY_012", "POLICY_013", "POLICY_014",
                "POLICY_015", "POLICY_016",
            ]
            for _cid in _CONTENT_CHECKS:
                self._add_check(
                    _cid, CheckCategory.PRIVACY_POLICY, CheckStatus.MANUAL_REVIEW_NEEDED,
                    details="Требуется ручная проверка: текст политики недоступен для "
                            "автоматического анализа (PDF-документ не удалось прочитать)",
                )
            self._add_check(
                "POLICY_017", CheckCategory.PRIVACY_POLICY,
                CheckStatus.PASS if pp.is_separate_page else CheckStatus.WARNING,
                details="Политика на отдельной странице: " + ("да" if pp.is_separate_page else "нет"),
            )
            return

        # Content checks: (check_id, value, title, message)
        # severity, law_reference, recommendation берутся из JSON через fallback в _add_violation()
        content_checks: list[tuple] = [
            ("POLICY_003", pp.has_operator_name, "Полное наименование оператора", None),
            ("POLICY_004", pp.has_inn_ogrn, "ИНН/ОГРН оператора",
             "В политике ПДн не указан ИНН/ОГРН оператора."),
            ("POLICY_005", pp.has_responsible_person, "Контакт ответственного за обработку ПДн",
             "Не указан контакт ответственного за обработку ПДн."),
            ("POLICY_006", pp.has_data_categories, "Категории персональных данных", None),
            ("POLICY_007", pp.has_purposes, "Цели обработки", None),
            ("POLICY_008", pp.has_legal_basis, "Правовые основания", None),
            ("POLICY_009", pp.has_retention_periods, "Сроки хранения", None),
            ("POLICY_010", pp.has_subject_rights, "Права субъектов ПДн", None),
            ("POLICY_011", pp.has_rights_procedure, "Порядок реализации прав", None),
            ("POLICY_012", pp.has_cross_border_info, "Информация о трансграничной передаче", None),
            ("POLICY_013", pp.has_security_measures, "Меры безопасности", None),
            ("POLICY_014", pp.has_cookie_info, "Информация о cookies", None),
            ("POLICY_015", pp.has_localization_statement, "Локализация данных на территории РФ",
             "Не указана локализация данных на территории РФ (ст. 18 ч. 5)."),
            ("POLICY_016", pp.has_date, "Дата публикации/обновления", None),
        ]
        for check_id, value, title, message in content_checks:
            # Cross-border is N/A if not applicable
            if check_id == "POLICY_012":
                status = CheckStatus.PASS if value else CheckStatus.WARNING
            else:
                status = CheckStatus.PASS if value else CheckStatus.FAIL
            self._add_check(check_id, CheckCategory.PRIVACY_POLICY, status, details=title)
            if status == CheckStatus.FAIL:
                self._add_violation(
                    check_id,
                    message or f"В Политике отсутствует: {title}",
                    message or f"Политика обработки ПДн не содержит обязательного раздела: {title}.",
                    category=CheckCategory.PRIVACY_POLICY, page_url=pp.url,
                )

        # POLICY_017: is separate page
        self._add_check(
            "POLICY_017", CheckCategory.PRIVACY_POLICY,
            CheckStatus.PASS if pp.is_separate_page else CheckStatus.WARNING,
            details="Политика на отдельной странице: " + ("да" if pp.is_separate_page else "нет"),
        )

    # ── Technical checks ─────────────────────────────────────────

    def _check_technical(self) -> None:
        # TECH_001: HTTPS
        self._add_check(
            "TECH_001", CheckCategory.TECHNICAL,
            CheckStatus.PASS if self.scan.ssl_info.has_ssl else CheckStatus.FAIL,
            details="HTTPS " + ("активен" if self.scan.ssl_info.has_ssl else "не настроен"),
        )
        if not self.scan.ssl_info.has_ssl:
            self._add_violation(
                "TECH_001", "Сайт не использует HTTPS",
                "Сайт не защищён SSL-сертификатом.",
                category=CheckCategory.TECHNICAL, page_url=self.scan.url,
            )

        # TECH_002-006: prohibited external services
        prohibited = [s for s in self.scan.external_scripts if s.is_prohibited]
        service_names = set()
        for script in prohibited:
            if script.service_name:
                service_names.add(script.service_name)

        prohibited_checks = {
            "TECH_002": ("Google Fonts", "Локально размещённые шрифты или fonts.cdnfonts.com"),
            "TECH_003": ("Google Analytics", "Яндекс.Метрика"),
            "TECH_004": ("Facebook Pixel", "VK Pixel, myTarget"),
            "TECH_005": ("Google reCAPTCHA", "Яндекс SmartCaptcha"),
            "TECH_006": ("Google Tag Manager", "Яндекс Метрика контейнер"),
        }

        for check_id, (service, alt) in prohibited_checks.items():
            found = service in service_names
            self._add_check(
                check_id, CheckCategory.TECHNICAL,
                CheckStatus.FAIL if found else CheckStatus.PASS,
                details=f"{service}: {'обнаружен' if found else 'не обнаружен'}",
            )
            if found:
                self._add_violation(
                    check_id, f"Использование запрещённого сервиса: {service}",
                    f"На сайте обнаружено подключение {service}, что запрещено с 01.07.2025.",
                    category=CheckCategory.TECHNICAL,
                    recommendation=f"Заменить {service} на {alt}.",
                )

    # ── Regulatory checks ────────────────────────────────────────

    def _check_regulatory(self) -> None:
        # These require manual verification
        self._add_check(
            "REG_001", CheckCategory.REGULATORY, CheckStatus.MANUAL_CHECK,
            details="Требуется ручная проверка: оператор зарегистрирован в реестре РКН",
        )
        self._add_check(
            "REG_002", CheckCategory.REGULATORY, CheckStatus.MANUAL_CHECK,
            details="Требуется ручная проверка: уведомление об обработке ПДн подано в РКН",
        )
        self._add_check(
            "REG_003", CheckCategory.REGULATORY, CheckStatus.MANUAL_CHECK,
            details="Требуется ручная проверка: хостинг на территории РФ",
        )

    # ── Tracker checks ───────────────────────────────────────────

    def _check_trackers(self) -> None:
        """TRACKER_001: tracker found but not mentioned in policy text.
        TRACKER_002: foreign tracker found, no cross-border transfer disclosure.

        Both checks are NOT_APPLICABLE when the privacy policy is not found.
        Source data: ScanResult.external_scripts (populated by static crawler or Playwright).
        Legal basis: TRACKER_001 — ст. 18.1 152-ФЗ; TRACKER_002 — ст. 12 152-ФЗ.
        """
        pp = self.scan.privacy_policy
        script_domains = [s.domain for s in self.scan.external_scripts if s.domain]
        found_trackers = find_trackers_in_scripts(script_domains)

        if not pp.found:
            for check_id in ("TRACKER_001", "TRACKER_002"):
                self._add_check(
                    check_id, CheckCategory.TRACKERS, CheckStatus.NOT_APPLICABLE,
                    details="Не применимо: политика обработки ПДн не найдена на сайте",
                )
            return

        policy_text = (pp.text or "").lower()

        # ── TRACKER_001 ───────────────────────────────────────────
        if not pp.text:
            self._add_check(
                "TRACKER_001", CheckCategory.TRACKERS, CheckStatus.NOT_APPLICABLE,
                details="Не применимо: текст политики ПДн недоступен для анализа",
            )
        else:
            undisclosed = [
                t for t in found_trackers
                if not any(kw in policy_text for kw in t["keywords"])
            ]
            if undisclosed:
                names = ", ".join(t["name"] for t in undisclosed)
                self._add_check(
                    "TRACKER_001", CheckCategory.TRACKERS, CheckStatus.FAIL,
                    details=f"Не упомянуты в политике: {names}",
                )
                self._add_violation(
                    "TRACKER_001",
                    "Трекеры не упомянуты в политике обработки ПДн",
                    f"На сайте подключены сервисы, обрабатывающие данные пользователей, "
                    f"но они не упоминаются в политике обработки ПДн: {names}. "
                    f"Субъект ПДн не был проинформирован об этих обработчиках.",
                    category=CheckCategory.TRACKERS, page_url=pp.url,
                    recommendation=f"Добавить в политику ПДн раздел о третьих лицах, которым передаются данные, "
                    f"с указанием каждого сервиса: {names}.",
                )
            else:
                self._add_check(
                    "TRACKER_001", CheckCategory.TRACKERS, CheckStatus.PASS,
                    details="Все обнаруженные трекеры упомянуты в политике ПДн"
                    if found_trackers else "Трекеры из реестра не обнаружены",
                )

        # ── TRACKER_002 ───────────────────────────────────────────
        has_cross_border = any(kw in policy_text for kw in _CROSS_BORDER_KEYWORDS)
        foreign_undisclosed = [
            t for t in found_trackers
            if t["is_foreign"] and not has_cross_border
        ]
        if foreign_undisclosed:
            names = ", ".join(t["name"] for t in foreign_undisclosed)
            self._add_check(
                "TRACKER_002", CheckCategory.TRACKERS, CheckStatus.FAIL,
                details=f"Иностранные трекеры без раскрытия трансграничной передачи: {names}",
            )
            self._add_violation(
                "TRACKER_002",
                "Иностранные трекеры без раскрытия трансграничной передачи данных",
                f"На сайте подключены иностранные сервисы ({names}), передающие данные "
                f"пользователей за рубеж, однако политика обработки ПДн не содержит "
                f"информации о трансграничной передаче данных.",
                category=CheckCategory.TRACKERS, page_url=pp.url,
                recommendation="Добавить в политику ПДн раздел о трансграничной передаче данных "
                "с указанием стран назначения и правовых оснований передачи "
                f"для следующих сервисов: {names}.",
            )
        else:
            self._add_check(
                "TRACKER_002", CheckCategory.TRACKERS, CheckStatus.PASS,
                details="Трансграничная передача данных раскрыта в политике"
                if (has_cross_border and any(t["is_foreign"] for t in found_trackers))
                else "Иностранные трекеры из реестра не обнаружены",
            )

    # ── LLM analysis ─────────────────────────────────────────────

    async def _analyze_policy_with_llm(self) -> str | None:
        """Deep LLM analysis of the privacy policy text.

        Includes web-verified legal context for up-to-date requirements.
        """
        pp = self.scan.privacy_policy
        if not pp.text:
            return None

        try:
            text = pp.text

            # Enhance system prompt with web context if available
            system = PRIVACY_POLICY_ANALYSIS_SYSTEM
            if self._web_legal_context:
                system += (
                    "\n\nАКТУАЛЬНЫЙ ПРАВОВОЙ КОНТЕКСТ (из интернета, учитывай при анализе):\n"
                    + self._web_legal_context[:3000]
                )

            result = await call_llm(
                system_prompt=system,
                user_prompt=PRIVACY_POLICY_ANALYSIS_USER.format(
                    site_url=self.scan.url,
                    policy_text=text,
                ),
                max_tokens=4096,
            )
            return result
        except Exception as e:
            logger.error("LLM policy analysis failed: %s", e)
            return None

    @staticmethod
    def _strip_llm_preamble(text: str) -> str:
        import re
        for i, line in enumerate(text.splitlines()):
            if re.match(r'[А-ЯЁа-яё]', line):
                return "\n".join(text.splitlines()[i:])
        return ""

    async def _generate_summary(self) -> str:
        """Generate a human-readable summary using LLM.

        Includes web-verified legal context for up-to-date fine amounts and requirements.
        """
        forms_without = sum(
            1 for f in self.scan.forms
            if f.collects_personal_data and not f.has_consent_checkbox
        )
        prohibited_count = sum(1 for s in self.scan.external_scripts if s.is_prohibited)
        violations_text = "\n".join(
            f"- [{v.severity.value}] {v.title}: {v.description}"
            for v in self.violations[:15]
        )

        # Enhance system prompt with web context
        system = SCAN_ANALYSIS_SYSTEM
        if self._web_legal_context:
            system += (
                "\n\nАКТУАЛЬНЫЙ ПРАВОВОЙ КОНТЕКСТ (из интернета, учитывай при формировании резюме):\n"
                + self._web_legal_context[:3000]
            )

        try:
            result = await call_llm(
                system_prompt=system,
                user_prompt=SCAN_ANALYSIS_USER.format(
                    site_url=self.scan.url,
                    pages_scanned=self.scan.pages_scanned,
                    forms_count=len(self.scan.forms),
                    forms_without_consent=forms_without,
                    prohibited_scripts=prohibited_count,
                    cookie_banner_status="обнаружен" if self.scan.cookie_banner.found else "не обнаружен",
                    privacy_policy_status="найдена" if self.scan.privacy_policy.found else "не найдена",
                    https_status="да" if self.scan.ssl_info.has_ssl else "нет",
                    violations_summary=violations_text or "Нарушений не обнаружено",
                ),
                max_tokens=4096,
            )
            return self._strip_llm_preamble(result)
        except Exception as e:
            logger.error("LLM summary generation failed: %s", e)
            return self._fallback_summary()

    def _fallback_summary(self) -> str:
        critical = sum(1 for v in self.violations if v.severity == Severity.CRITICAL)
        high = sum(1 for v in self.violations if v.severity == Severity.HIGH)
        return (
            f"Обнаружено нарушений: {len(self.violations)} "
            f"(критических: {critical}, высокой серьёзности: {high}). "
            f"Требуется детальная проверка."
        )

    # ── Helpers ───────────────────────────────────────────────────

    def _build_scan_limitations(self) -> list[str]:
        """Build list of scan limitation warnings based on what was (not) found."""
        notes: list[str] = []
        pd_forms = [f for f in self.scan.forms if f.collects_personal_data]

        if not pd_forms:
            notes.append(
                "Формы: не обнаружены статичным парсером — если сайт использует "
                "клиентский рендеринг (React/Vue/Next.js), формы могут существовать, "
                "но рисоваться через JavaScript. Требуется ручная проверка."
            )
            notes.append(
                "Чекбоксы согласия: не могут быть проверены автоматически — "
                "требуют ручной верификации на страницах с формами."
            )
        else:
            notes.append(
                "Чекбоксы согласия: в SPA-формах (React/Vue) динамические атрибуты "
                "могут не совпадать с HTML-снимком — рекомендуется ручная проверка."
            )

        if not self.scan.cookie_banner.found or not self.scan.cookie_banner.has_accept_button:
            notes.append(
                "Cookie-баннер: определяется косвенно по тегам <script src> — "
                "если баннер монтируется JS-библиотекой (OneTrust, Cookiebot и др.), "
                "кнопки «Принять»/«Отклонить» могут быть не распознаны. "
                "Рекомендуется проверка в браузере."
            )

        # PDF policy without extractable text
        pp = self.scan.privacy_policy
        if pp.found and not pp.text:
            notes.append(
                "Политика ПДн найдена в формате PDF, но текст не удалось извлечь "
                "(возможно, сканированный документ или защищённый PDF). "
                "Содержимое политики требует ручной проверки."
            )

        # Prepend crawler-level notes (e.g. Playwright fallback reason)
        return self.scan.scan_limitations + notes

    def _calculate_risk_level(self, score: int, violations: list[Violation]) -> Severity:
        critical_count = sum(1 for v in violations if v.severity == Severity.CRITICAL)
        if critical_count >= 3 or score < 30:
            return Severity.CRITICAL
        if critical_count >= 1 or score < 50:
            return Severity.HIGH
        if score < 75:
            return Severity.MEDIUM
        return Severity.LOW

    def _add_check(
        self,
        check_id: str,
        category: CheckCategory,
        status: CheckStatus,
        details: str | None = None,
        severity: str | None = None,
    ) -> None:
        """Add a check item from the checklist."""
        check_def = get_check_by_id(check_id)
        if check_def and severity is None:
            severity = check_def.get("severity", "medium")
        elif severity is None:
            severity = "medium"

        self.checklist.append(CheckItem(
            id=check_id,
            category=category,
            title=check_def.get("title", check_id) if check_def else check_id,
            description=check_def.get("description", "") if check_def else "",
            status=status,
            severity=Severity(severity),
            details=details,
            law_reference=check_def.get("law_reference") if check_def else None,
            recommendation=check_def.get("recommendation") if check_def else None,
        ))

    def _add_violation(
        self,
        check_id: str,
        title: str,
        description: str,
        severity: Severity | str | None = None,
        category: CheckCategory = CheckCategory.TECHNICAL,
        page_url: str | None = None,
        law_reference: str | None = None,
        recommendation: str | None = None,
    ) -> None:
        check_def = get_check_by_id(check_id)
        if check_def:
            if severity is None:
                severity = check_def.get("severity", "medium")
            if law_reference is None:
                law_reference = check_def.get("law_reference", "")
            if recommendation is None:
                recommendation = check_def.get("recommendation", "")
        else:
            if severity is None:
                severity = "medium"
            if law_reference is None:
                law_reference = ""
            if recommendation is None:
                recommendation = ""

        if not isinstance(severity, Severity):
            severity = Severity(severity)

        fine_range = None
        if severity in (Severity.CRITICAL, Severity.HIGH):
            if check_def and check_def.get("fine_reference"):
                fine = get_fine_by_id(check_def["fine_reference"])
                if fine:
                    fine_range = f'{fine["first_offense_min"]:,} – {fine["first_offense_max"]:,} руб.'

        self.violations.append(Violation(
            check_id=check_id,
            title=title,
            description=description,
            severity=severity,
            category=category,
            page_url=page_url,
            law_reference=law_reference,
            fine_range=fine_range,
            recommendation=recommendation,
        ))


async def analyze_site(
    scan_result: ScanResult,
    enable_web_verification: bool = True,
) -> ComplianceReport:
    """Convenience function to run compliance analysis."""
    analyzer = ComplianceAnalyzer(scan_result, enable_web_verification=enable_web_verification)
    return await analyzer.analyze()
