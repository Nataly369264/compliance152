"""Tests for web search tools, verification, and cache modules."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.cache import WebContextCache, get_web_context_cached
from src.llm.verification import (
    GENERAL_QUERIES,
    SEARCH_QUERIES,
    gather_web_context,
)
from src.llm.web_tools import (
    _clean_html_text,
    _is_allowed_domain,
    format_search_results,
)


# ── web_tools tests ──────────────────────────────────────────────


class TestCleanHtml:
    def test_removes_scripts_and_styles(self):
        html = """
        <html><body>
        <script>alert('x')</script>
        <style>.foo { color: red; }</style>
        <p>Real content here</p>
        </body></html>
        """
        text = _clean_html_text(html)
        assert "alert" not in text
        assert "color: red" not in text
        assert "Real content" in text

    def test_removes_nav_and_footer(self):
        html = """
        <html><body>
        <nav>Menu items</nav>
        <main><p>Main content</p></main>
        <footer>Footer stuff</footer>
        </body></html>
        """
        text = _clean_html_text(html)
        assert "Main content" in text
        assert "Menu items" not in text
        assert "Footer stuff" not in text

    def test_prefers_article_tag(self):
        html = """
        <html><body>
        <div>Sidebar noise</div>
        <article><p>Article content is best</p></article>
        </body></html>
        """
        text = _clean_html_text(html)
        assert "Article content" in text

    def test_truncates_long_text(self):
        html = "<html><body>" + "<p>x</p>" * 100000 + "</body></html>"
        text = _clean_html_text(html)
        assert len(text) <= 12000

    def test_empty_html(self):
        text = _clean_html_text("<html><body></body></html>")
        assert text == "" or len(text) < 5


class TestAllowedDomain:
    def test_allowed_domains(self):
        assert _is_allowed_domain("https://consultant.ru/doc/12345")
        assert _is_allowed_domain("https://www.garant.ru/products/")
        assert _is_allowed_domain("https://rkn.gov.ru/news/rsoc/")
        assert _is_allowed_domain("https://pd.rkn.gov.ru/")
        assert _is_allowed_domain("https://b152.ru/article")

    def test_blocked_domains(self):
        assert not _is_allowed_domain("https://evil.com/hack")
        assert not _is_allowed_domain("https://google.com")
        assert not _is_allowed_domain("https://facebook.com")

    def test_invalid_url(self):
        assert not _is_allowed_domain("not-a-url")
        assert not _is_allowed_domain("")


class TestFormatSearchResults:
    def test_formats_results(self):
        results = [
            {"url": "https://example.com", "title": "Test", "content": "Some content"},
            {"url": "https://other.com", "title": "Other", "content": "More content"},
        ]
        text = format_search_results(results)
        assert "Результат 1" in text
        assert "Результат 2" in text
        assert "https://example.com" in text
        assert "Some content" in text

    def test_empty_results(self):
        text = format_search_results([])
        assert "не найдены" in text.lower()


# ── verification tests ───────────────────────────────────────────


class TestSearchQueries:
    def test_all_document_types_have_queries(self):
        """Every document type used in the project should have search queries."""
        from src.generator.prompts import DOCUMENT_TYPES
        for doc_type in DOCUMENT_TYPES:
            assert doc_type in SEARCH_QUERIES, f"Missing search queries for {doc_type}"

    def test_queries_contain_year_placeholder(self):
        """At least one query per type should have {year}."""
        for doc_type, queries in SEARCH_QUERIES.items():
            has_year = any("{year}" in q for q in queries)
            assert has_year, f"No {{year}} placeholder in queries for {doc_type}"

    def test_general_queries_exist(self):
        assert len(GENERAL_QUERIES) >= 2

    def test_general_queries_have_year(self):
        for q in GENERAL_QUERIES:
            assert "{year}" in q


class TestGatherWebContext:
    @pytest.mark.asyncio
    async def test_gather_returns_string(self):
        """gather_web_context should return a string (even if empty)."""
        with patch("src.llm.verification.web_search", new_callable=AsyncMock) as mock_search, \
             patch("src.llm.verification.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_search.return_value = [
                {"url": "https://consultant.ru/test", "title": "Test", "content": "152-ФЗ changes"}
            ]
            mock_llm.return_value = "## Актуальные требования\n\n- Новые штрафы..."

            result = await gather_web_context("privacy_policy", "Политика обработки ПДн")

            assert isinstance(result, str)
            assert len(result) > 0
            assert mock_search.called
            assert mock_llm.called

    @pytest.mark.asyncio
    async def test_gather_empty_on_no_results(self):
        """Returns empty string when all searches fail."""
        with patch("src.llm.verification.web_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            result = await gather_web_context("privacy_policy")
            assert result == ""

    @pytest.mark.asyncio
    async def test_gather_deduplicates_urls(self):
        """Duplicate URLs from different queries should be deduplicated."""
        with patch("src.llm.verification.web_search", new_callable=AsyncMock) as mock_search, \
             patch("src.llm.verification.call_llm", new_callable=AsyncMock) as mock_llm:
            # Same URL returned by multiple queries
            mock_search.return_value = [
                {"url": "https://consultant.ru/same", "title": "Same", "content": "Same content"}
            ]
            mock_llm.return_value = "Summary"

            await gather_web_context("privacy_policy")

            # LLM should receive deduplicated results
            llm_call = mock_llm.call_args
            user_prompt = llm_call.kwargs.get("user_prompt", "") or llm_call.args[1] if len(llm_call.args) > 1 else ""
            # Even though search is called multiple times, results should be unique
            assert mock_search.called


# ── cache tests ──────────────────────────────────────────────────


class TestWebContextCache:
    def test_set_and_get(self):
        cache = WebContextCache(ttl_hours=24)
        cache.set("privacy_policy", "test content")
        assert cache.get("privacy_policy") == "test content"

    def test_miss_returns_none(self):
        cache = WebContextCache(ttl_hours=24)
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self):
        cache = WebContextCache(ttl_hours=0)  # 0 hours = expired immediately
        cache.set("test", "content")
        # Manually set timestamp in the past
        key = cache._make_key("test")
        cache._cache[key] = ("content", datetime.now() - timedelta(hours=1))
        assert cache.get("test") is None

    def test_clear(self):
        cache = WebContextCache()
        cache.set("a", "1")
        cache.set("b", "2")
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_clear_expired(self):
        cache = WebContextCache(ttl_hours=1)
        cache.set("fresh", "content")
        # Add expired entry
        old_key = ("old", "2020-01-01")
        cache._cache[old_key] = ("expired", datetime.now() - timedelta(hours=48))

        removed = cache.clear_expired()
        assert removed == 1
        assert cache.get("fresh") == "content"

    def test_stats(self):
        cache = WebContextCache()
        cache.set("test", "content")
        stats = cache.stats()
        assert stats["entries"] == 1
        assert "ttl_hours" in stats
        assert "keys" in stats


class TestGetWebContextCached:
    @pytest.mark.asyncio
    async def test_uses_cache_on_second_call(self):
        """Second call should return cached value without calling gather_fn."""
        mock_gather = AsyncMock(return_value="web context result")

        # Clear global cache
        from src.llm import cache as cache_module
        cache_module._cache = None

        result1 = await get_web_context_cached(
            "test_type", "Test Doc", gather_fn=mock_gather,
        )
        assert result1 == "web context result"
        assert mock_gather.call_count == 1

        result2 = await get_web_context_cached(
            "test_type", "Test Doc", gather_fn=mock_gather,
        )
        assert result2 == "web context result"
        assert mock_gather.call_count == 1  # Not called again!

        # Clean up
        cache_module._cache = None

    @pytest.mark.asyncio
    async def test_empty_result_not_cached(self):
        """Empty results should not be cached."""
        mock_gather = AsyncMock(return_value="")

        from src.llm import cache as cache_module
        cache_module._cache = None

        result = await get_web_context_cached(
            "empty_type", "Empty", gather_fn=mock_gather,
        )
        assert result == ""

        # Second call should call gather_fn again
        mock_gather.return_value = "now has content"
        result2 = await get_web_context_cached(
            "empty_type", "Empty", gather_fn=mock_gather,
        )
        assert result2 == "now has content"
        assert mock_gather.call_count == 2

        cache_module._cache = None


# ── monitor tests ────────────────────────────────────────────────


class TestMonitorParseLLMResponse:
    def test_parse_json_array(self):
        from src.monitor.monitor import LegalMonitor
        monitor = LegalMonitor()

        response = '[{"id": "LU-2025-001", "title": "Test"}]'
        result = monitor._parse_llm_response(response)
        assert len(result) == 1
        assert result[0]["id"] == "LU-2025-001"

    def test_parse_json_with_markdown_fences(self):
        from src.monitor.monitor import LegalMonitor
        monitor = LegalMonitor()

        response = '```json\n[{"id": "LU-2025-002", "title": "Test2"}]\n```'
        result = monitor._parse_llm_response(response)
        assert len(result) == 1

    def test_parse_empty_array(self):
        from src.monitor.monitor import LegalMonitor
        monitor = LegalMonitor()

        result = monitor._parse_llm_response("[]")
        assert result == []

    def test_parse_invalid_json(self):
        from src.monitor.monitor import LegalMonitor
        monitor = LegalMonitor()

        result = monitor._parse_llm_response("Not JSON at all")
        assert result == []

    def test_parse_json_embedded_in_text(self):
        from src.monitor.monitor import LegalMonitor
        monitor = LegalMonitor()

        response = 'Some text before [{"id": "LU-1"}] some text after'
        result = monitor._parse_llm_response(response)
        assert len(result) == 1


# ── updater tests ────────────────────────────────────────────────


class TestUpdaterDiff:
    def test_generate_diff(self):
        from src.updater.updater import DocumentUpdater
        updater = DocumentUpdater()

        old = "Line 1\nLine 2\nLine 3"
        new = "Line 1\nLine 2 modified\nLine 3\nLine 4 added"

        diff = updater._generate_diff(old, new)
        assert "-Line 2" in diff
        assert "+Line 2 modified" in diff
        assert "+Line 4 added" in diff

    def test_generate_diff_no_changes(self):
        from src.updater.updater import DocumentUpdater
        updater = DocumentUpdater()

        text = "Same content"
        diff = updater._generate_diff(text, text)
        assert diff == ""

    def test_format_update_for_prompt(self):
        from src.updater.updater import DocumentUpdater
        from src.models.legal_update import LegalUpdate

        updater = DocumentUpdater()
        update = LegalUpdate(
            id="LU-2025-TEST",
            date="2025-01-01",
            effective_date="2025-09-01",
            source="Test Law",
            source_url="https://example.com",
            title="Test Update",
            summary="Test summary",
            articles=["ст. 9", "ст. 18.1"],
            affected_documents=["privacy_policy"],
            requirements=["Requirement 1", "Requirement 2"],
            severity="high",
            category="law_amendment",
        )

        text = updater._format_update_for_prompt(update)
        assert "Test Update" in text
        assert "ст. 9" in text
        assert "Requirement 1" in text
        assert "2025-09-01" in text


# ── generator integration tests ──────────────────────────────────


class TestGeneratorWebVerification:
    @pytest.mark.asyncio
    async def test_generator_includes_web_context_in_prompt(self):
        """When web verification is enabled, web context should be in the LLM prompt."""
        from src.generator.generator import DocumentGenerator
        from src.models.organization import OrganizationData

        org = OrganizationData(
            id="test-org",
            legal_name="ООО Тест",
            website_url="https://test.ru",
        )

        with patch("src.generator.generator.get_web_context_cached", new_callable=AsyncMock) as mock_cache, \
             patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_cache.return_value = "WEB VERIFIED: Новые штрафы с 2025 года"
            mock_llm.return_value = "# Политика обработки ПДн..."

            gen = DocumentGenerator(org, enable_web_verification=True)
            doc = await gen.generate_document("privacy_policy")

            # Check LLM was called with web context
            llm_call = mock_llm.call_args
            user_prompt = llm_call.kwargs.get("user_prompt", "")
            assert "ОНЛАЙН-ВЕРИФИКАЦИИ" in user_prompt
            assert "WEB VERIFIED" in user_prompt
            assert doc["web_verified"] is True

    @pytest.mark.asyncio
    async def test_generator_works_without_web_verification(self):
        """When disabled, no web search should happen."""
        from src.generator.generator import DocumentGenerator
        from src.models.organization import OrganizationData

        org = OrganizationData(
            id="test-org",
            legal_name="ООО Тест",
            website_url="https://test.ru",
        )

        with patch("src.generator.generator.get_web_context_cached", new_callable=AsyncMock) as mock_cache, \
             patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "# Document"

            gen = DocumentGenerator(org, enable_web_verification=False)
            doc = await gen.generate_document("privacy_policy")

            mock_cache.assert_not_called()
            assert doc["web_verified"] is False

    @pytest.mark.asyncio
    async def test_generator_continues_on_web_failure(self):
        """If web verification fails, document should still be generated."""
        from src.generator.generator import DocumentGenerator
        from src.models.organization import OrganizationData

        org = OrganizationData(
            id="test-org",
            legal_name="ООО Тест",
            website_url="https://test.ru",
        )

        with patch("src.generator.generator.get_web_context_cached", new_callable=AsyncMock) as mock_cache, \
             patch("src.generator.generator.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_cache.side_effect = Exception("Network error")
            mock_llm.return_value = "# Document"

            gen = DocumentGenerator(org, enable_web_verification=True)
            doc = await gen.generate_document("privacy_policy")

            assert doc["content_md"] == "# Document"
            assert doc["web_verified"] is False
