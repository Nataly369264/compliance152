"""Tests for TelegramNotifier: graceful degradation, retry logic, api-error early exit."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.notifier.telegram import TelegramNotifier


# ── Helpers ───────────────────────────────────────────────────────

def _mock_http_client(response: MagicMock | None = None, side_effect=None) -> AsyncMock:
    """Build an async context-manager mock for httpx.AsyncClient."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if side_effect is not None:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        client.post = AsyncMock(return_value=response)
    return client


# ── Tests ─────────────────────────────────────────────────────────

async def test_no_token_graceful():
    """Without token/chat_id all methods return False and never raise."""
    notifier = TelegramNotifier(token="", chat_id="")

    result_alert = await notifier.send_critical_alert({"source_id": "x", "threat_score": 3})
    result_digest = await notifier.send_digest("weekly digest text")

    assert result_alert is False
    assert result_digest is False


async def test_retry_on_http_error():
    """httpx.HTTPError triggers all 3 retry attempts, then returns False."""
    mock_client = _mock_http_client(side_effect=httpx.HTTPError("connection failed"))

    with (
        patch("src.notifier.telegram.httpx.AsyncClient", return_value=mock_client),
        patch("src.notifier.telegram.asyncio.sleep", new_callable=AsyncMock),
    ):
        notifier = TelegramNotifier(token="fake_token", chat_id="12345")
        result = await notifier._send("test message")

    assert result is False
    assert mock_client.post.call_count == 3  # _RETRY_COUNT = 3


async def test_no_retry_on_api_error():
    """Telegram API returns ok=False → exactly 1 attempt, no retry."""
    api_response = MagicMock()
    api_response.raise_for_status = MagicMock()
    api_response.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}

    mock_client = _mock_http_client(response=api_response)

    with patch("src.notifier.telegram.httpx.AsyncClient", return_value=mock_client):
        notifier = TelegramNotifier(token="fake_token", chat_id="12345")
        result = await notifier._send("test message")

    assert result is False
    assert mock_client.post.call_count == 1
