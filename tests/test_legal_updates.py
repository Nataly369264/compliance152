"""Tests for legal updates loading, filtering, and integration with generator."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.knowledge.loader import (
    format_legal_context,
    get_active_updates,
    get_updates_for_document,
    get_updates_for_document_active,
    load_legal_updates,
)
from src.generator.generator import DocumentGenerator
from src.models.legal_update import LegalUpdate
from src.models.organization import OrganizationData


# ── Loading ───────────────────────────────────────────────────────

def test_load_legal_updates():
    updates = load_legal_updates()
    assert len(updates) == 8
    assert all(isinstance(u, LegalUpdate) for u in updates)


def test_legal_update_fields():
    updates = load_legal_updates()
    for u in updates:
        assert u.id
        assert u.date
        assert u.effective_date
        assert u.source
        assert u.title
        assert u.summary
        assert len(u.articles) > 0
        assert len(u.affected_documents) > 0
        assert len(u.requirements) > 0
        assert u.severity in ("critical", "high", "medium", "low")


# ── Filtering by document type ────────────────────────────────────

def test_get_updates_for_privacy_policy():
    updates = get_updates_for_document("privacy_policy")
    assert len(updates) >= 2
    ids = [u.id for u in updates]
    assert "LU-2025-001" in ids  # Prohibited services
    assert "LU-2025-002" in ids  # Separate consent
    assert "LU-2025-006" in ids  # Localization


def test_get_updates_for_incident_instruction():
    updates = get_updates_for_document("incident_instruction")
    ids = [u.id for u in updates]
    assert "LU-2025-004" in ids  # 24-hour notification
    assert "LU-2025-005" in ids  # Turnover fines


def test_get_updates_for_consent_form():
    updates = get_updates_for_document("consent_form")
    ids = [u.id for u in updates]
    assert "LU-2025-002" in ids  # Separate consent document


def test_get_updates_for_nonexistent_type():
    updates = get_updates_for_document("nonexistent_doc_type")
    assert updates == []


# ── Filtering by effective date ───────────────────────────────────

def test_get_active_updates_all():
    """With a future date, all updates should be active."""
    updates = get_active_updates(as_of=date(2026, 1, 1))
    assert len(updates) == 8


def test_get_active_updates_before_july_2025():
    """Before July 2025, only early-2025 updates are active."""
    updates = get_active_updates(as_of=date(2025, 6, 1))
    ids = [u.id for u in updates]
    assert "LU-2025-005" in ids  # May 30 fines
    assert "LU-2025-008" in ids  # March 1 ISPDn
    assert "LU-2025-001" not in ids  # July 1 - not yet
    assert "LU-2025-002" not in ids  # September 1 - not yet


def test_get_active_updates_august_2025():
    """After July but before September — 4 updates active."""
    updates = get_active_updates(as_of=date(2025, 8, 15))
    ids = [u.id for u in updates]
    assert "LU-2025-001" in ids  # July 1 ✓
    assert "LU-2025-005" in ids  # May 30 ✓
    assert "LU-2025-008" in ids  # March 1 ✓
    assert "LU-2025-002" not in ids  # September 1 - not yet


def test_get_updates_for_document_active_combined():
    """Privacy policy + date filter."""
    updates = get_updates_for_document_active(
        "privacy_policy", as_of=date(2025, 8, 1),
    )
    ids = [u.id for u in updates]
    # LU-2025-001 (July 1, affects privacy_policy) — should be included
    assert "LU-2025-001" in ids
    # LU-2025-002 (Sep 1, affects privacy_policy) — not yet
    assert "LU-2025-002" not in ids
    # LU-2025-006 (Sep 1, affects privacy_policy) — not yet
    assert "LU-2025-006" not in ids


# ── Formatting ────────────────────────────────────────────────────

def test_format_legal_context_empty():
    result = format_legal_context([])
    assert result == ""


def test_format_legal_context_single():
    updates = [LegalUpdate(
        id="TEST-001",
        date="2025-09-01",
        effective_date="2025-09-01",
        source="Test Law",
        title="Test Change",
        summary="Something changed",
        articles=["ст. 9 152-ФЗ"],
        affected_documents=["privacy_policy"],
        requirements=["Do X", "Do Y"],
        severity="critical",
        category="test",
    )]
    result = format_legal_context(updates)
    assert "АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА" in result
    assert "Test Change" in result
    assert "2025-09-01" in result
    assert "Test Law" in result
    assert "ст. 9 152-ФЗ" in result
    assert "Something changed" in result
    assert "Do X" in result
    assert "Do Y" in result


def test_format_legal_context_sorted_by_date_desc():
    updates = [
        LegalUpdate(
            id="OLD", date="2025-01-01", effective_date="2025-01-01",
            source="A", title="Old Change", summary="Old",
            articles=["a"], affected_documents=["x"], requirements=["r"],
        ),
        LegalUpdate(
            id="NEW", date="2025-09-01", effective_date="2025-09-01",
            source="B", title="New Change", summary="New",
            articles=["b"], affected_documents=["x"], requirements=["r"],
        ),
    ]
    result = format_legal_context(updates)
    # New change should appear before old change
    assert result.index("New Change") < result.index("Old Change")


def test_format_legal_context_real_data():
    """Format real updates for privacy_policy and check it's non-trivial."""
    updates = get_updates_for_document("privacy_policy")
    result = format_legal_context(updates)
    assert len(result) > 200
    assert "152-ФЗ" in result


# ── Integration: generator receives legal context ─────────────────

def _make_org() -> OrganizationData:
    return OrganizationData(
        id="org-test-001",
        legal_name='ООО "Тест"',
        website_url="https://test.ru",
    )


@pytest.mark.asyncio
async def test_generator_injects_legal_context():
    """Verify the generator includes legal context in LLM prompt."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("privacy_policy")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    # privacy_policy has multiple updates, so legal context should be present
    assert "АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА" in user_prompt
    assert "Запрет использования зарубежных сервисов" in user_prompt


@pytest.mark.asyncio
async def test_generator_legal_context_contains_requirements():
    """Verify specific requirements appear in the prompt."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("consent_form")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    # consent_form is affected by LU-2025-002 (separate consent)
    assert "отдельный" in user_prompt.lower() or "самостоятельный" in user_prompt.lower()


@pytest.mark.asyncio
async def test_generator_no_legal_context_for_clean_type():
    """Document types with no matching updates get empty legal context."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    # Temporarily patch updates to return empty
    with (
        patch("src.generator.generator.get_updates_for_document_active", return_value=[]),
        patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_llm.return_value = "doc"
        await gen.generate_document("privacy_policy")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    assert "АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА" not in user_prompt


@pytest.mark.asyncio
async def test_system_prompt_has_legal_context_instruction():
    """System prompt should instruct LLM to prioritize legal context."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("privacy_policy")

    system_prompt = mock_llm.call_args.kwargs["system_prompt"]
    assert "АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА" in system_prompt
