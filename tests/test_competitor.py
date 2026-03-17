"""Tests for competitor fetcher, diff engine, and keyword pre-filter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.monitor.competitor import (
    RAW_TEXT_LIMIT,
    _build_diff,
    _classify_diff,
    _fetch_url,
)


# ── Helpers ───────────────────────────────────────────────────────

def _make_mock_client(response: MagicMock) -> AsyncMock:
    """Build an async context-manager mock for httpx.AsyncClient."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    return client


def _ok_response(html: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


# ── Fetch tests ───────────────────────────────────────────────────

async def test_fetch_snapshot_success():
    """Fetched content is truncated to RAW_TEXT_LIMIT (10 240 chars)."""
    big_text = "а" * 20_000
    big_html = f"<html><body><main>{big_text}</main></body></html>"

    mock_client = _make_mock_client(_ok_response(big_html))

    with patch("src.monitor.competitor.httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_url("https://example.com")

    assert result.status == "ok"
    assert len(result.text) <= RAW_TEXT_LIMIT
    assert result.content_hash != ""


async def test_fetch_snapshot_antibot():
    """HTTP 403 → status='blocked', no exception raised, text is empty."""
    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {"content-type": "text/html"}
    resp.raise_for_status = MagicMock()

    mock_client = _make_mock_client(resp)

    with (
        patch("src.monitor.competitor.httpx.AsyncClient", return_value=mock_client),
        patch("src.monitor.competitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _fetch_url("https://example.com")

    assert result.status == "blocked"
    assert result.text == ""


# ── Diff tests ────────────────────────────────────────────────────

def test_diff_detects_change():
    """Two different texts produce a non-empty unified diff."""
    diff = _build_diff("old content here", "new content here")
    assert diff != ""


def test_diff_no_change():
    """Identical texts produce an empty diff."""
    diff = _build_diff("same content", "same content")
    assert diff == ""


# ── Pre-filter tests ──────────────────────────────────────────────

def test_prefilter_blocks_irrelevant():
    """Diff without any meaningful keywords → LLM not needed (has_meaningful=False)."""
    irrelevant = "- foo bar baz qux\n+ foo bar qux baz\n"
    has_meaningful, change_type = _classify_diff(irrelevant)

    assert has_meaningful is False
    assert change_type == "minor"
