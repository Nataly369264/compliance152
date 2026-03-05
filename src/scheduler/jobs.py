"""APScheduler jobs for Competitor Intelligence Monitor + NPA check.

Single scheduler instance — do NOT create a second one anywhere else.

Jobs:
  run_npa_check()       — LegalMonitor.check_npa_sources() + send_critical_alert per alert
  run_competitor_check() — run_competitor_check() from competitor.py (full pipeline)
  run_digest()          — DigestReporter.build_digest() → send(), save to DB

Cron expressions are loaded from config/sources.yaml::scheduler (not hardcoded).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.monitor.competitor import run_competitor_check as _run_competitor_check
from src.monitor.monitor import LegalMonitor
from src.monitor.reporter import DigestReporter
from src.notifier.telegram import TelegramNotifier
from src.storage.database import get_db

logger = logging.getLogger(__name__)

_SOURCES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sources.yaml"


# ── Individual jobs ───────────────────────────────────────────────

async def run_npa_check() -> list[dict]:
    """Check all NPA sources and send critical alerts immediately.

    Returns list of critical alert dicts (may be empty).
    """
    db = await get_db()
    notifier = TelegramNotifier()
    monitor = LegalMonitor()
    try:
        alerts = await monitor.check_npa_sources(db)
        for alert in alerts:
            await notifier.send_critical_alert(alert)
        logger.info("run_npa_check complete: %d critical alerts sent", len(alerts))
        return alerts
    except Exception as exc:
        logger.error("run_npa_check failed: %s", exc)
        return []


async def run_competitor_check() -> int:
    """Fetch all competitors, diff, LLM-analyse meaningful changes.

    Returns count of LLM-analysed changes.
    """
    db = await get_db()
    try:
        count = await _run_competitor_check(db)
        logger.info("run_competitor_check complete: %d LLM-analysed changes", count)
        return count
    except Exception as exc:
        logger.error("run_competitor_check failed: %s", exc)
        return 0


async def run_digest() -> bool:
    """Build and send weekly digest from all pending (undigested) changes.

    Flow:
      1. Load pending changes from DB
      2. Split into NPA and competitor buckets
      3. Build Markdown digest
      4. Send via Telegram
      5. Mark changes as digested, save digest record

    Returns True if digest was built and sent successfully.
    """
    db = await get_db()
    notifier = TelegramNotifier()
    reporter = DigestReporter()
    try:
        all_pending = await db.list_pending_changes()
        npa = [c for c in all_pending if c.get("change_type") == "npa" or c.get("npa_critical") == 1]
        comps = [c for c in all_pending if c not in npa]

        digest = reporter.build_digest(npa, comps)
        if not digest:
            logger.info("run_digest: nothing to send")
            return False

        sent = await reporter.send(notifier)
        if sent:
            change_ids = [c["id"] for c in all_pending]
            await db.mark_changes_digested(change_ids)
            period_end = date.today().isoformat()
            period_start = (date.today() - timedelta(days=7)).isoformat()
            await db.save_digest(period_start, period_end, digest)
            logger.info("run_digest: digest sent and saved (period %s – %s)", period_start, period_end)
            return True

        logger.warning("run_digest: digest built but Telegram send failed")
        return False
    except Exception as exc:
        logger.error("run_digest failed: %s", exc)
        return False


# ── Scheduler factory ─────────────────────────────────────────────

def _load_cron() -> dict[str, str]:
    """Load scheduler cron expressions from config/sources.yaml."""
    try:
        with open(_SOURCES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("scheduler", {})
    except Exception as exc:
        logger.error("Failed to load scheduler config from sources.yaml: %s", exc)
        return {}


def create_scheduler() -> AsyncIOScheduler:
    """Create the single APScheduler instance with all three jobs.

    Cron expressions come from sources.yaml::scheduler.
    Fallback defaults match sources.yaml documented values.
    """
    cron = _load_cron()

    npa_cron = cron.get("npa_check", "0 9 * * *")          # daily 09:00
    comp_cron = cron.get("competitor_check", "0 9 * * 1")   # Monday 09:00
    digest_cron = cron.get("digest_generate", "0 10 * * 1") # Monday 10:00

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_npa_check,
        CronTrigger.from_crontab(npa_cron),
        id="npa_check",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        run_competitor_check,
        CronTrigger.from_crontab(comp_cron),
        id="competitor_check",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        run_digest,
        CronTrigger.from_crontab(digest_cron),
        id="digest_generate",
        misfire_grace_time=3600,
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured: npa_check='%s', competitor_check='%s', digest_generate='%s'",
        npa_cron, comp_cron, digest_cron,
    )
    return scheduler
