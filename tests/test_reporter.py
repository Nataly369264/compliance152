"""Tests for DigestReporter: build_digest output and send skip logic."""
from __future__ import annotations

from unittest.mock import AsyncMock

from src.monitor.reporter import DigestReporter
from src.notifier.telegram import TelegramNotifier


# ── Helpers ───────────────────────────────────────────────────────

def _change(
    source_id: str = "src1",
    change_type: str = "npa",
    threat_score: int | None = 3,
    npa_critical: int = 0,  # DB stores 0/1/NULL
    diff_summary: str = "Изменение в нормативном акте",
) -> dict:
    return {
        "id": 1,
        "source_id": source_id,
        "url": "https://example.com",
        "diff_summary": diff_summary,
        "change_type": change_type,
        "threat_score": threat_score,
        "npa_critical": npa_critical,
    }


# ── Tests ─────────────────────────────────────────────────────────

def test_build_digest_empty():
    """Both lists empty → build_digest returns None."""
    reporter = DigestReporter()
    result = reporter.build_digest([], [])
    assert result is None


def test_build_digest_critical_first():
    """npa_critical=1 entry appears before npa_critical=0 in NPA section, regardless of score."""
    # Non-critical has higher threat_score but should appear second
    non_critical = _change(source_id="non_crit", threat_score=5, npa_critical=0)
    critical = _change(source_id="crit", threat_score=2, npa_critical=1)

    reporter = DigestReporter()
    digest = reporter.build_digest([non_critical, critical], [])

    assert digest is not None
    # Critical rows get the "⚠️ *КРИТИЧНО*" badge; non-critical rows don't
    pos_badge = digest.index("КРИТИЧНО")
    pos_non_critical = digest.index("non_crit")
    assert pos_badge < pos_non_critical


async def test_send_skips_on_none():
    """When build_digest returned None, send() returns False and never calls notifier."""
    reporter = DigestReporter()
    reporter.build_digest([], [])  # sets _digest = None

    mock_notifier = AsyncMock(spec=TelegramNotifier)
    result = await reporter.send(mock_notifier)

    assert result is False
    mock_notifier.send_digest.assert_not_called()
