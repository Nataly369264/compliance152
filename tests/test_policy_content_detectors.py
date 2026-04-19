"""Tests for policy content keyword detectors (has_legal_basis, has_rights_procedure,
has_security_measures).

These patterns live inline in crawler.py and playwright_crawler.py.
Tests use real-world formulations from el-ed.ru policy.pdf to guard against
regressions when patterns are updated.

Pattern strings below must mirror those in crawler.py / playwright_crawler.py.
If the crawler patterns change, update the constants here too.
"""
from __future__ import annotations

import re

import pytest

# ── Pattern constants (mirrors crawler.py) ───────────────────────────────────

_LEGAL_BASIS_PAT = re.compile(
    r"(правов.{0,20}основан|закон.{0,20}основан|legal.?basis|на основании"
    r"|основан.{0,20}обработк)",
    re.DOTALL,
)

_RIGHTS_PROC_PAT = re.compile(
    r"(порядок.{0,20}реализац|порядок.{0,20}обращен|10.{0,10}рабочих|направить.{0,20}запрос"
    r"|направить.{0,20}(обращен|заявлен)|требова.{0,30}(уточнени|блокировани|уничтожени))",
    re.DOTALL,
)

_SECURITY_PAT = re.compile(
    r"(мер.{0,20}безопасност|мер.{0,20}защит|security.?measure|шифрован|encrypt"
    r"|режим.{0,20}защит|защит.{0,10}конфиденциальн)",
    re.DOTALL,
)


# ── has_legal_basis ───────────────────────────────────────────────────────────

class TestLegalBasisDetector:
    def test_existing_pattern_pravovye_osnovaniya(self):
        """Existing pattern — section 5.1 of el-ed.ru policy (position ~23k)."""
        text = "3) иные правовые основания, предусмотренные законодательством."
        assert _LEGAL_BASIS_PAT.search(text.lower())

    def test_new_pattern_osnovaniem_dlya_obrabotki(self):
        """New pattern — section 3.4 cookies of el-ed.ru (within first 20k).

        Formulation: 'Основанием для обработки ваших данных в этом случае
        будет согласие на обработку персональных данных, содержащихся в cookies.'
        """
        text = "Основанием для обработки ваших данных в этом случае будет согласие."
        assert _LEGAL_BASIS_PAT.search(text.lower())

    def test_new_pattern_keyword_at_position_beyond_20k(self):
        """Legal-basis keyword at position ~25k must be found (regression for 40k limit).

        Before raising the limit, the pattern at pos 23k+ was silently truncated.
        """
        filler = "обработка персональных данных пользователя. " * 470  # >20k chars
        keyword = " правовые основания для обработки персональных данных"
        long_text = (filler + keyword).lower()
        keyword_pos = long_text.find("правовые")
        assert keyword_pos > 20000, f"keyword must be beyond 20k, got pos={keyword_pos}"
        assert _LEGAL_BASIS_PAT.search(long_text)

    def test_no_match_on_unrelated_text(self):
        text = "оператор собирает и хранит персональную информацию пользователей."
        assert not _LEGAL_BASIS_PAT.search(text)


# ── has_rights_procedure ──────────────────────────────────────────────────────

class TestRightsProcedureDetector:
    def test_new_pattern_napravit_pismennoe_obrashchenie(self):
        """New pattern — section 1.3 of el-ed.ru (within first 20k).

        Formulation: 'Пользователь вправе направить письменное обращение
        по адресу электронной почты.'
        """
        text = "Пользователь вправе направить письменное обращение по адресу электронной почты."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_new_pattern_napravit_zayavlenie_ob_otzyve(self):
        """New pattern — section 9.3.3 of el-ed.ru policy (position ~32k).

        Formulation: 'Направить Оператору заявление об отзыве своего согласия
        на обработку персональных данных.'
        """
        text = "Направить Оператору заявление об отзыве своего согласия на обработку."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_new_pattern_trebovat_utochneniya(self):
        """New pattern — section 9.3.2 of el-ed.ru policy (position ~31k).

        Formulation: '9.3.2. Требовать от Оператора уточнения своих
        персональных данных, их блокирования или уничтожения.'
        """
        text = "9.3.2. Требовать от Оператора уточнения своих персональных данных."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_new_pattern_trebovat_blokirovaniya(self):
        """Variant of 9.3.2 — блокирования."""
        text = "Пользователь вправе требовать блокирования персональных данных."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_new_pattern_trebovat_unichtozheniya(self):
        """Variant of 9.3.2 — уничтожения."""
        text = "Субъект персональных данных вправе требовать уничтожения своих данных."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_existing_pattern_10_rabochikh_dney(self):
        """Existing pattern — common formulation in Russian privacy policies."""
        text = "Ответ предоставляется в течение 10 рабочих дней с момента обращения."
        assert _RIGHTS_PROC_PAT.search(text.lower())

    def test_no_match_on_operator_obligations_only(self):
        """Text about operator obligations without procedure details must not match."""
        text = "оператор обеспечивает хранение конфиденциальной информации в тайне."
        assert not _RIGHTS_PROC_PAT.search(text)


# ── has_security_measures ─────────────────────────────────────────────────────

class TestSecurityMeasuresDetector:
    def test_new_pattern_rezhim_zashchity(self):
        """New pattern — section 2.1 of el-ed.ru (within first 20k).

        Formulation: 'обязательства Администратора сайта по неразглашению
        и обеспечению режима защиты конфиденциальности персональных данных.'
        """
        text = "обязательства по обеспечению режима защиты конфиденциальности персональных данных."
        assert _SECURITY_PAT.search(text.lower())

    def test_new_pattern_zashchita_konfidentsialnosti(self):
        """New pattern — section 2.1 of el-ed.ru (within first 20k)."""
        text = "обеспечению режима защиты конфиденциальности персональных данных."
        assert _SECURITY_PAT.search(text.lower())

    def test_existing_pattern_mery_zashchity_heading(self):
        """Existing pattern — section 7 heading of el-ed.ru (position ~27k)."""
        text = "7. МЕРЫ ЗАЩИТЫ ПЕРСОНАЛЬНЫХ ДАННЫХ."
        assert _SECURITY_PAT.search(text.lower())

    def test_existing_pattern_mery_bezopasnosti(self):
        """Existing pattern — common Russian formulation."""
        text = "Оператор принимает необходимые меры безопасности для защиты данных."
        assert _SECURITY_PAT.search(text.lower())

    def test_no_match_on_data_collection_text(self):
        """Data collection text without security mention must not match."""
        text = "оператор собирает технические данные устройств пользователей."
        assert not _SECURITY_PAT.search(text)
