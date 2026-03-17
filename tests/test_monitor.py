"""Tests for LegalMonitor.check_npa_sources: fallback path and parse_warning logic."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from src.monitor.competitor import FetchResult, NpaSourceConfig
from src.monitor.monitor import LegalMonitor


# ── Helpers ───────────────────────────────────────────────────────

def _make_source(**kwargs) -> NpaSourceConfig:
    defaults = dict(
        id="test_src",
        name="Test Source",
        url="https://example.com/npa",
        type="html_list",
        npa_critical=True,
        keywords=["персональн"],
    )
    defaults.update(kwargs)
    return NpaSourceConfig(**defaults)


def _make_db(last_snapshot=None, recent_snapshots=None) -> AsyncMock:
    db = AsyncMock()
    db.get_last_snapshot.return_value = last_snapshot
    db.get_recent_snapshots.return_value = recent_snapshots or []
    db.save_snapshot.return_value = 1
    db.save_change.return_value = 1
    return db


# ── Tests ─────────────────────────────────────────────────────────

async def test_check_npa_sources_fallback():
    """LLM returns None → save_change called with threat_score=None, npa_critical=None."""
    source = _make_source()

    # Old snapshot has different hash → triggers diff path
    old_snapshot = {"content_hash": "oldhash", "raw_text": "старый текст персональные данные"}
    new_text = "новый текст персональные данные изменения приказ"
    ok_fetch = FetchResult(url=source.url, status="ok", text=new_text, content_hash="newhash")

    db = _make_db(last_snapshot=old_snapshot)

    with (
        patch("src.monitor.monitor.load_sources", return_value=([], [source])),
        patch("src.monitor.monitor._fetch_url", new_callable=AsyncMock, return_value=ok_fetch),
        patch("src.monitor.monitor._analyze_npa_diff_with_llm", new_callable=AsyncMock, return_value=None),
    ):
        monitor = LegalMonitor()
        alerts = await monitor.check_npa_sources(db)

    assert alerts == []
    db.save_change.assert_called_once()
    kw = db.save_change.call_args.kwargs
    assert kw["threat_score"] is None
    assert kw["npa_critical"] is None


async def test_parse_warning_threshold(caplog):
    """found_items=0 with 3 prior snapshots that had content → parse_warning logged."""
    source = _make_source()
    empty_fetch = FetchResult(url=source.url, status="ok", text="", content_hash="emptyhash")
    # Three previous snapshots with non-empty content
    recent = [
        {"raw_text": "строка один\nстрока два"},
        {"raw_text": "строка три\nстрока четыре"},
        {"raw_text": "строка пять\nстрока шесть"},
    ]

    db = _make_db(last_snapshot=None, recent_snapshots=recent)

    caplog.set_level(logging.WARNING, logger="src.monitor.monitor")

    with (
        patch("src.monitor.monitor.load_sources", return_value=([], [source])),
        patch("src.monitor.monitor._fetch_url", new_callable=AsyncMock, return_value=empty_fetch),
    ):
        monitor = LegalMonitor()
        await monitor.check_npa_sources(db)

    assert any("parse_warning" in record.message for record in caplog.records)


async def test_parse_warning_quiet_day(caplog):
    """found_items=0 when prior snapshots were also empty → no parse_warning logged."""
    source = _make_source()
    empty_fetch = FetchResult(url=source.url, status="ok", text="", content_hash="emptyhash")
    # Three previous snapshots also empty
    recent = [
        {"raw_text": ""},
        {"raw_text": ""},
        {"raw_text": ""},
    ]

    db = _make_db(last_snapshot=None, recent_snapshots=recent)

    caplog.set_level(logging.WARNING, logger="src.monitor.monitor")

    with (
        patch("src.monitor.monitor.load_sources", return_value=([], [source])),
        patch("src.monitor.monitor._fetch_url", new_callable=AsyncMock, return_value=empty_fetch),
    ):
        monitor = LegalMonitor()
        await monitor.check_npa_sources(db)

    assert not any("parse_warning" in record.message for record in caplog.records)
