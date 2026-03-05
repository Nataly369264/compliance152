"""Digest reporter for Competitor Intelligence Monitor.

Builds a unified Markdown digest from NPA changes and competitor changes,
then sends it via TelegramNotifier.

Usage (from scheduler):
    reporter = DigestReporter()
    digest = reporter.build_digest(npa_changes, competitor_changes)
    if digest:
        await reporter.send(notifier)
        await db.mark_changes_digested(change_ids)
        await db.save_digest(period_start, period_end, digest)
"""
from __future__ import annotations

import logging
from datetime import date

from src.notifier.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# Threat score → human-readable label
_THREAT_LABEL: dict[int, str] = {
    5: "🔴 КРИТИЧНО (5/5)",
    4: "🟠 ВЫСОКИЙ (4/5)",
    3: "🟡 СРЕДНИЙ (3/5)",
    2: "🟢 НИЗКИЙ (2/5)",
    1: "⚪ МИНИМАЛЬНЫЙ (1/5)",
}

_DIVIDER = "─" * 24


def _threat_label(score: int | None) -> str:
    if score is None:
        return "❓ не определён"
    return _THREAT_LABEL.get(score, f"❓ {score}/5")


def _change_type_ru(change_type: str) -> str:
    return {
        "npa":     "НПА",
        "feature": "функция",
        "pricing": "цены",
        "ui":      "интерфейс",
        "minor":   "незначительное",
    }.get(change_type, change_type)


def _format_change(change: dict, critical_badge: bool = False) -> str:
    """Format a single competitor_changes row as a Markdown block.

    change dict fields used:
      source_id, url, diff_summary, change_type, threat_score, npa_critical
    npa_critical is stored as INTEGER in DB: 1 = True, 0 = False, NULL = None.
    """
    source_id = change.get("source_id", "?")
    url = change.get("url", "")
    summary = change.get("diff_summary") or "_описание не сформировано_"
    change_type = _change_type_ru(change.get("change_type") or "?")
    score = change.get("threat_score")

    header = f"⚠️ *КРИТИЧНО*  •  `{source_id}`" if critical_badge else f"`{source_id}`"
    meta = f"_Тип: {change_type}  •  Угроза: {_threat_label(score)}_"
    link = f"🔗 {url}" if url else ""

    parts = [header, meta, summary]
    if link:
        parts.append(link)
    return "\n".join(parts)


class DigestReporter:
    """Builds and sends a unified Markdown digest of monitoring changes.

    Workflow:
      1. Call build_digest(npa_changes, competitor_changes)
         — returns the Markdown string, or None if both lists are empty.
      2. Call await send(notifier) to deliver the built digest via Telegram.
         — returns False if no digest was built or send fails.
    """

    def __init__(self) -> None:
        self._digest: str | None = None

    def build_digest(
        self,
        npa_changes: list[dict],
        competitor_changes: list[dict],
    ) -> str | None:
        """Build a Markdown digest string.

        npa_changes       — rows with change_type='npa' or npa_critical=1
        competitor_changes — rows from competitor sources (feature/pricing/ui/minor)

        npa_critical=1 rows are placed first in the NPA section with ⚠️ КРИТИЧНО badge.
        Empty sections are written as "_нет изменений за период_" (not hidden).

        Returns the Markdown string, or None if both lists are empty.
        """
        if not npa_changes and not competitor_changes:
            logger.info("DigestReporter: both lists empty — digest skipped")
            self._digest = None
            return None

        today = date.today().strftime("%d.%m.%Y")

        # Overall threat score = max across all records (None-safe)
        all_scores = [
            c["threat_score"]
            for c in (npa_changes + competitor_changes)
            if c.get("threat_score") is not None
        ]
        max_score = max(all_scores) if all_scores else None

        lines: list[str] = [
            f"📋 *Дайджест изменений — {today}*",
            "",
            f"Итоговый уровень угрозы: {_threat_label(max_score)}",
            "",
            _DIVIDER,
            "",
            "🏛 *НПА — нормативные изменения*",
            "",
        ]

        # ── NPA section ────────────────────────────────────────────
        if not npa_changes:
            lines.append("_нет изменений за период_")
        else:
            # npa_critical=1 rows first; within each group: threat_score DESC (None last)
            def _sort_key(c: dict) -> tuple:
                is_critical = 1 if c.get("npa_critical") == 1 else 0
                score = c.get("threat_score") or 0
                return (-is_critical, -score)

            for change in sorted(npa_changes, key=_sort_key):
                is_critical = change.get("npa_critical") == 1
                lines.append(_format_change(change, critical_badge=is_critical))
                lines.append("")

        lines += ["", _DIVIDER, "", "🏢 *Конкуренты*", ""]

        # ── Competitors section ────────────────────────────────────
        if not competitor_changes:
            lines.append("_нет изменений за период_")
        else:
            sorted_competitors = sorted(
                competitor_changes,
                key=lambda c: c.get("threat_score") or 0,
                reverse=True,
            )
            for change in sorted_competitors:
                lines.append(_format_change(change))
                lines.append("")

        self._digest = "\n".join(lines).rstrip()
        logger.info(
            "DigestReporter: digest built (%d NPA, %d competitor, max_score=%s)",
            len(npa_changes), len(competitor_changes), max_score,
        )
        return self._digest

    async def send(self, notifier: TelegramNotifier) -> bool:
        """Send the built digest via TelegramNotifier.

        Returns False if no digest has been built yet, or if send fails.
        """
        if not self._digest:
            logger.info("DigestReporter.send: no digest to send — skipping")
            return False
        return await notifier.send_digest(self._digest)
