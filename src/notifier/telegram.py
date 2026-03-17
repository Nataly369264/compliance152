"""Telegram notifier for compliance152.

Two public methods:
  send_critical_alert(alert)  — immediate notification for npa_critical changes (Stage 3)
  send_digest(digest)         — weekly/daily digest from reporter (Stage 5)

Both are async and fail gracefully: if the token is not configured, a warning
is logged and the call returns False without raising.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

# Telegram Bot API
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# sendMessage hard limit is 4096 chars; leave headroom for formatting
_MAX_TEXT_LEN = 4000

_RETRY_COUNT = 3
_RETRY_DELAY = 2.0  # seconds between retries

# Threat score → emoji label
_THREAT_LABEL = {
    5: "🔴 КРИТИЧНО",
    4: "🟠 ВЫСОКИЙ",
    3: "🟡 СРЕДНИЙ",
    2: "🟢 НИЗКИЙ",
    1: "⚪ МИНИМАЛЬНЫЙ",
}


def _threat_label(score: int) -> str:
    return _THREAT_LABEL.get(score, f"❓ {score}")


def _format_critical_alert(alert: dict) -> str:
    """Format npa_critical alert dict as a Telegram Markdown message."""
    score = alert.get("threat_score", 0)
    label = _threat_label(score)
    source_name = alert.get("source_name", alert.get("source_id", "?"))
    url = alert.get("url", "")
    summary = alert.get("summary", "нет описания")
    action_required = alert.get("action_required", False)
    action = alert.get("action", "")

    lines = [
        f"⚠️ *НПА — критическое изменение*",
        f"",
        f"*Источник:* {source_name}",
        f"*Уровень угрозы:* {label}",
        f"*URL:* {url}",
        f"",
        f"*Изменение:*",
        summary,
    ]

    if action_required and action:
        lines += ["", f"*Требуемые действия:*", action]

    return "\n".join(lines)


def _truncate(text: str, max_len: int = _MAX_TEXT_LEN) -> str:
    """Truncate text to Telegram message limit, appending a notice if cut."""
    if len(text) <= max_len:
        return text
    suffix = "\n\n_...сообщение обрезано (превышен лимит Telegram)_"
    return text[: max_len - len(suffix)] + suffix


class TelegramNotifier:
    """Sends Telegram messages via Bot API.

    Usage:
        notifier = TelegramNotifier()
        await notifier.send_critical_alert(alert_dict)
        await notifier.send_digest(markdown_text)
    """

    def __init__(
        self,
        token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._configured = bool(token and chat_id)

        if not self._configured:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — "
                "notifications will be skipped"
            )

    async def send_critical_alert(self, alert: dict) -> bool:
        """Send an immediate npa_critical change alert.

        alert dict expected keys (from LegalMonitor.check_npa_sources):
          source_id, source_name, url, summary, threat_score,
          action_required, action

        Returns True if sent successfully, False otherwise.
        """
        if not self._configured:
            return False

        text = _truncate(_format_critical_alert(alert))
        return await self._send(text, parse_mode="Markdown")

    async def send_digest(self, digest: str) -> bool:
        """Send a formatted Markdown digest (from Stage 5 reporter).

        Returns True if sent successfully, False otherwise.
        """
        if not self._configured:
            return False

        text = _truncate(digest)
        return await self._send(text, parse_mode="Markdown")

    async def _send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """POST to Telegram sendMessage with retry logic.

        Retries up to _RETRY_COUNT times on httpx.HTTPError,
        sleeping _RETRY_DELAY seconds between attempts.
        Returns True on success, False after all retries exhausted.
        """
        url = _API_BASE.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        for attempt in range(1, _RETRY_COUNT + 1):
            try:
                async with httpx.AsyncClient(
                    trust_env=False,  # CRITICAL: bypass Windows proxy
                    timeout=15,
                ) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()

                result = response.json()
                if result.get("ok"):
                    logger.info("Telegram message sent (chat_id=%s)", self._chat_id)
                    return True

                # API returned ok=false — non-retriable (bad token, chat not found, etc.)
                logger.error(
                    "Telegram API error: %s", result.get("description", "unknown")
                )
                return False

            except httpx.HTTPError as e:
                logger.warning(
                    "Telegram send failed (attempt %d/%d): %s",
                    attempt, _RETRY_COUNT, e,
                )
                if attempt < _RETRY_COUNT:
                    await asyncio.sleep(_RETRY_DELAY)

            except Exception as e:
                logger.error("Unexpected error sending Telegram message: %s", e)
                return False

        logger.error(
            "Telegram: all %d attempts failed for chat_id=%s", _RETRY_COUNT, self._chat_id
        )
        return False
