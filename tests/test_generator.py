"""Tests for document generator."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.generator.generator import DocumentGenerator, generate_documents, TEMPLATES_DIR
from src.generator.prompts import (
    DOCUMENT_TYPES,
    FULL_PACKAGE,
    PUBLIC_DOCUMENTS,
)
from src.models.organization import OrganizationData


# ── Fixtures ──────────────────────────────────────────────────────

def _make_org(**overrides) -> OrganizationData:
    defaults = {
        "id": "org-test-001",
        "legal_name": 'ООО "Тестовая Компания"',
        "short_name": "Тестовая Компания",
        "inn": "7701234567",
        "ogrn": "1027700123456",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "actual_address": "г. Москва, ул. Тестовая, д. 1",
        "ceo_name": "Иванов Иван Иванович",
        "ceo_position": "Генеральный директор",
        "responsible_person": "Петров Пётр Петрович",
        "responsible_contact": "dpo@test-company.ru",
        "website_url": "https://test-company.ru",
        "email": "info@test-company.ru",
        "phone": "+7 (495) 123-45-67",
        "data_categories": ["ФИО", "email", "телефон", "адрес"],
        "processing_purposes": ["Исполнение договора", "Маркетинг"],
        "data_subjects": ["Клиенты", "Сотрудники"],
        "third_parties": ["1С-Битрикс", "Яндекс.Метрика"],
        "cross_border": False,
        "hosting_location": "Российская Федерация",
        "info_systems": ["1С:Предприятие", "Битрикс24"],
    }
    defaults.update(overrides)
    return OrganizationData(**defaults)


# ── Document types config ─────────────────────────────────────────

def test_document_types_structure():
    """Each document type must have title, template_file, description."""
    assert len(DOCUMENT_TYPES) == 12
    for key, info in DOCUMENT_TYPES.items():
        assert "title" in info, f"{key} missing title"
        assert "template_file" in info, f"{key} missing template_file"
        assert "description" in info, f"{key} missing description"
        assert isinstance(info["title"], str)
        assert isinstance(info["description"], str)


def test_public_documents_are_subset():
    for doc in PUBLIC_DOCUMENTS:
        assert doc in DOCUMENT_TYPES, f"{doc} not in DOCUMENT_TYPES"


def test_full_package_covers_all():
    assert set(FULL_PACKAGE) == set(DOCUMENT_TYPES.keys())


# ── Template loading ──────────────────────────────────────────────

def test_templates_dir_exists():
    assert TEMPLATES_DIR.exists(), f"Templates dir not found: {TEMPLATES_DIR}"


def test_all_template_files_exist():
    """Every document type with a non-None template_file must have the file on disk."""
    for key, info in DOCUMENT_TYPES.items():
        tpl = info["template_file"]
        if tpl is not None:
            path = TEMPLATES_DIR / tpl
            assert path.exists(), f"Template file missing for {key}: {path}"


def test_templates_contain_placeholders():
    """Templates should contain {{...}} placeholders."""
    for key, info in DOCUMENT_TYPES.items():
        tpl = info["template_file"]
        if tpl is None:
            continue
        content = (TEMPLATES_DIR / tpl).read_text(encoding="utf-8")
        assert "{{" in content, f"Template {tpl} has no placeholders"
        assert len(content) > 200, f"Template {tpl} is suspiciously short"


def test_load_template_existing():
    org = _make_org()
    gen = DocumentGenerator(org)
    content = gen._load_template("privacy_policy.md")
    assert "{{LEGAL_NAME}}" in content
    assert len(content) > 500


def test_load_template_none():
    org = _make_org()
    gen = DocumentGenerator(org)
    content = gen._load_template(None)
    assert "Шаблон отсутствует" in content


def test_load_template_missing_file():
    org = _make_org()
    gen = DocumentGenerator(org)
    content = gen._load_template("nonexistent_file.md")
    assert "не найден" in content


# ── Document generation (mocked LLM) ─────────────────────────────

@pytest.mark.asyncio
async def test_generate_single_document():
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "# Политика обработки ПДн\n\nТекст документа..."
        doc = await gen.generate_document("privacy_policy")

    assert doc["doc_type"] == "privacy_policy"
    assert doc["title"] == "Политика обработки персональных данных"
    assert doc["content_md"] == "# Политика обработки ПДн\n\nТекст документа..."
    assert doc["organization_id"] == "org-test-001"
    assert doc["version"] == 1
    assert "id" in doc
    assert "created_at" in doc

    # Check LLM was called with correct args
    mock_llm.assert_called_once()
    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs["max_tokens"] == 8192
    assert call_kwargs.kwargs["temperature"] == 0.2


@pytest.mark.asyncio
async def test_generate_document_unknown_type():
    org = _make_org()
    gen = DocumentGenerator(org)
    with pytest.raises(ValueError, match="Unknown document type"):
        await gen.generate_document("nonexistent_type")


@pytest.mark.asyncio
async def test_generate_public_documents():
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "# Документ\n\nТекст..."
        results = await gen.generate_public_documents()

    assert len(results) == len(PUBLIC_DOCUMENTS)
    for doc in results:
        assert "error" not in doc
        assert doc["doc_type"] in PUBLIC_DOCUMENTS
    assert mock_llm.call_count == len(PUBLIC_DOCUMENTS)


@pytest.mark.asyncio
async def test_generate_public_documents_partial_failure():
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    call_count = 0

    async def _mock_llm(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("LLM API error")
        return "# Документ\n\nТекст..."

    with patch("src.generator.generator.call_llm", side_effect=_mock_llm):
        results = await gen.generate_public_documents()

    assert len(results) == len(PUBLIC_DOCUMENTS)
    # When LLM fails, generator falls back to template — all results are valid docs
    fallbacks = [r for r in results if "content_md" in r and "⚠️" in r["content_md"]]
    successes = [r for r in results if "content_md" in r and "⚠️" not in r["content_md"]]
    assert len(fallbacks) == 1
    assert len(successes) == 2


@pytest.mark.asyncio
async def test_generate_full_package():
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "# Документ\n\nТекст..."
        results = await gen.generate_full_package()

    assert len(results) == len(FULL_PACKAGE)
    assert mock_llm.call_count == len(FULL_PACKAGE)


@pytest.mark.asyncio
async def test_generate_documents_convenience_full():
    org = _make_org()

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "# Документ\n\nТекст..."
        results = await generate_documents(org, doc_types=None, enable_web_verification=False)

    assert len(results) == len(FULL_PACKAGE)


@pytest.mark.asyncio
async def test_generate_documents_convenience_specific():
    org = _make_org()

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "# Документ\n\nТекст..."
        results = await generate_documents(org, doc_types=["privacy_policy", "cookie_policy"], enable_web_verification=False)

    assert len(results) == 2
    assert mock_llm.call_count == 2


@pytest.mark.asyncio
async def test_generate_documents_convenience_with_error():
    org = _make_org()

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [RuntimeError("fail"), "# Ок"]
        results = await generate_documents(org, doc_types=["privacy_policy", "cookie_policy"], enable_web_verification=False)

    assert len(results) == 2
    # When LLM fails, generator falls back to template — result is a valid doc with disclaimer
    assert "content_md" in results[0]
    assert "⚠️" in results[0]["content_md"]
    assert results[1]["content_md"] == "# Ок"


# ── LLM prompt content ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_receives_org_data_in_prompt():
    """Verify organization data is interpolated into the LLM prompt."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("privacy_policy")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    assert "ООО" in user_prompt
    assert "7701234567" in user_prompt
    assert "test-company.ru" in user_prompt
    assert "Иванов Иван Иванович" in user_prompt
    assert "ФИО" in user_prompt


@pytest.mark.asyncio
async def test_llm_receives_template_content():
    """Verify the template is included in the LLM prompt."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("privacy_policy")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    # Template should be included in the prompt
    assert "ПОЛИТИКА ОБРАБОТКИ ПЕРСОНАЛЬНЫХ ДАННЫХ" in user_prompt or "{{LEGAL_NAME}}" in user_prompt


@pytest.mark.asyncio
async def test_no_template_generates_from_scratch():
    """Documents without templates (employee_consent) get fallback instruction."""
    org = _make_org()
    gen = DocumentGenerator(org, enable_web_verification=False)

    with patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "doc"
        await gen.generate_document("employee_consent")

    user_prompt = mock_llm.call_args.kwargs["user_prompt"]
    assert "Шаблон отсутствует" in user_prompt
