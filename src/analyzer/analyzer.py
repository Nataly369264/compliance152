from __future__ import annotations

import logging
import uuid
from datetime import datetime

from src.knowledge.loader import (
    load_website_checklist,
    estimate_fines,
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
    Severity,
    Violation,
)
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
        self._check_cookies()
        self._check_privacy_policy()
        self._check_technical()
        self._check_regulatory()

        # LLM deep analysis of privacy policy text
        llm_analysis = None
        if self.scan.privacy_policy.found and self.scan.privacy_policy.text:
            llm_analysis = await self._analyze_policy_with_llm()

        # Calculate scores
        total = len(self.checklist)
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
                        Severity.CRITICAL, CheckCategory.FORMS, f.page_url,
                        "ст. 9 ч. 1 152-ФЗ",
                        "Добавить чекбокс согласия на обработку ПДн рядом с формой.",
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
                    Severity.CRITICAL, CheckCategory.FORMS, f.page_url,
                    "ст. 9 ч. 4 152-ФЗ",
                    "Убрать атрибут checked с чекбокса согласия — пользователь должен "
                    "проставить его самостоятельно (принцип активного согласия).",
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
                Severity.HIGH, CheckCategory.FORMS, None,
                "ст. 18.1 152-ФЗ",
                "Добавить ссылку на Политику обработки ПДн рядом с каждой формой.",
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
                Severity.CRITICAL, CheckCategory.COOKIES, self.scan.url,
                "ст. 9 152-ФЗ, 420-ФЗ",
                "Установить cookie-баннер с возможностью принятия и отклонения cookie.",
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
                Severity.HIGH, CheckCategory.COOKIES, self.scan.url,
                "ст. 9 152-ФЗ",
                "Добавить кнопку отказа ('Отклонить' / 'Только необходимые') "
                "наравне с кнопкой принятия.",
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
                Severity.HIGH, CheckCategory.COOKIES, self.scan.url,
                "ст. 9 152-ФЗ",
                "Загружать аналитику только после получения согласия на cookie.",
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
                Severity.CRITICAL, CheckCategory.PRIVACY_POLICY, self.scan.url,
                "ст. 18.1 152-ФЗ",
                "Опубликовать Политику обработки ПДн в общедоступном месте на сайте.",
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
                Severity.MEDIUM, CheckCategory.PRIVACY_POLICY, None,
                "ст. 18.1 152-ФЗ",
                "Добавить ссылку на Политику в футер каждой страницы.",
            )

        # Content checks (from parsed privacy policy)
        content_checks = [
            ("POLICY_003", pp.has_operator_name, "Полное наименование оператора"),
            ("POLICY_004", pp.has_inn_ogrn, "ИНН/ОГРН и адрес"),
            ("POLICY_005", pp.has_responsible_person, "Ответственный за обработку ПДн"),
            ("POLICY_006", pp.has_data_categories, "Категории персональных данных"),
            ("POLICY_007", pp.has_purposes, "Цели обработки"),
            ("POLICY_008", pp.has_legal_basis, "Правовые основания"),
            ("POLICY_009", pp.has_retention_periods, "Сроки хранения"),
            ("POLICY_010", pp.has_subject_rights, "Права субъектов ПДн"),
            ("POLICY_011", pp.has_rights_procedure, "Порядок реализации прав"),
            ("POLICY_012", pp.has_cross_border_info, "Информация о трансграничной передаче"),
            ("POLICY_013", pp.has_security_measures, "Меры безопасности"),
            ("POLICY_014", pp.has_cookie_info, "Информация о cookies"),
            ("POLICY_015", pp.has_localization_statement, "Заявление о локализации данных"),
            ("POLICY_016", pp.has_date, "Дата публикации/обновления"),
        ]
        for check_id, value, title in content_checks:
            # Cross-border is N/A if not applicable
            if check_id == "POLICY_012":
                status = CheckStatus.PASS if value else CheckStatus.WARNING
            else:
                status = CheckStatus.PASS if value else CheckStatus.FAIL
            self._add_check(check_id, CheckCategory.PRIVACY_POLICY, status, details=title)
            if status == CheckStatus.FAIL:
                self._add_violation(
                    check_id, f"В Политике отсутствует: {title}",
                    f"Политика обработки ПДн не содержит обязательного раздела: {title}.",
                    Severity.HIGH, CheckCategory.PRIVACY_POLICY, pp.url,
                    "ст. 18.1 152-ФЗ",
                    f"Добавить раздел '{title}' в Политику обработки ПДн.",
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
                Severity.CRITICAL, CheckCategory.TECHNICAL, self.scan.url,
                "ст. 19 152-ФЗ",
                "Установить SSL-сертификат и настроить HTTPS.",
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
                    Severity.CRITICAL, CheckCategory.TECHNICAL, None,
                    "ч. 5 ст. 18 152-ФЗ, 420-ФЗ",
                    f"Заменить {service} на {alt}.",
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

    # ── LLM analysis ─────────────────────────────────────────────

    async def _analyze_policy_with_llm(self) -> str | None:
        """Deep LLM analysis of the privacy policy text.

        Includes web-verified legal context for up-to-date requirements.
        """
        pp = self.scan.privacy_policy
        if not pp.text:
            return None

        try:
            # Truncate very long policies
            text = pp.text[:15000]

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
                max_tokens=2048,
            )
            return result
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

        return notes

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
    ) -> None:
        """Add a check item from the checklist."""
        checklist_items = {item["id"]: item for item in load_website_checklist()}
        item_data = checklist_items.get(check_id, {})

        self.checklist.append(CheckItem(
            id=check_id,
            category=category,
            title=item_data.get("title", check_id),
            description=item_data.get("description", ""),
            status=status,
            severity=Severity(item_data.get("severity", "medium")),
            details=details,
            law_reference=item_data.get("law_reference"),
            recommendation=item_data.get("recommendation"),
        ))

    def _add_violation(
        self,
        check_id: str,
        title: str,
        description: str,
        severity: Severity,
        category: CheckCategory,
        page_url: str | None,
        law_reference: str,
        recommendation: str,
    ) -> None:
        fine_range = None
        # Quick fine lookup
        if severity in (Severity.CRITICAL, Severity.HIGH):
            from src.knowledge.loader import load_fine_schedule
            schedule = load_fine_schedule()
            if check_id.startswith("FORM_"):
                for entry in schedule:
                    if "согласи" in entry.get("violation", "").lower():
                        fine_range = f"{entry['first_offense_min']:,} - {entry['first_offense_max']:,} руб."
                        break
            elif check_id.startswith("TECH_"):
                for entry in schedule:
                    if "зарубежн" in entry.get("violation", "").lower():
                        fine_range = f"{entry['first_offense_min']:,} - {entry['first_offense_max']:,} руб."
                        break

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
