"""Competitor Intelligence Monitor — fetcher + diff engine.

Stage 1: page fetching with antibot protection, SHA-256 deduplication,
diff building and keyword pre-filter. LLM analysis is wired in Stage 2.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import yaml
from bs4 import BeautifulSoup

from src.storage.database import Database

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────

RAW_TEXT_LIMIT = 10_240          # max bytes stored in DB per snapshot
FETCH_TIMEOUT = 25               # seconds per request
RETRY_COUNT = 3                  # attempts per URL
RETRY_BASE_DELAY = 2.0           # seconds, doubled each retry (exp backoff)
MIN_ANTIBOT_DELAY = 3.0          # seconds between requests (random)
MAX_ANTIBOT_DELAY = 8.0

SOURCES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sources.yaml"

# Keywords that make a diff worth LLM analysis (Stage 2).
# Any match → change_type = content-level; no match → 'minor'.
MEANINGFUL_KEYWORDS: list[str] = [
    "цена", "тариф", "стоимость", "функци", "проверк", "штраф",
    "новый", "новая", "добавил", "обновл", "расширен", "улучшен",
    "бесплатн", "попробуй", "trial", "feature", "price", "pricing",
]

# Rotating User-Agent pool (10 agents)
_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
]

FetchStatus = Literal["ok", "timeout", "blocked", "error"]


# ── Data structures ───────────────────────────────────────────────

@dataclass
class FetchResult:
    url: str
    status: FetchStatus
    text: str = ""          # cleaned text, max RAW_TEXT_LIMIT chars
    content_hash: str = ""  # SHA-256 of cleaned text


@dataclass
class DiffResult:
    source_id: str
    url: str
    old_text: str
    new_text: str
    unified_diff: str
    has_meaningful_changes: bool
    change_type: Literal["feature", "pricing", "ui", "npa", "minor"] = "minor"


@dataclass
class SourceConfig:
    """Parsed entry from sources.yaml."""
    id: str
    name: str
    urls: list[str]
    js_render: bool = False
    llm_analyze: bool = True
    watch: list[str] = field(default_factory=list)


# ── sources.yaml loader ───────────────────────────────────────────

def load_sources(path: Path = SOURCES_PATH) -> tuple[list[SourceConfig], list[dict]]:
    """Load and parse sources.yaml.

    Returns (competitors, npa_sources).
    """
    if not path.exists():
        logger.error("sources.yaml not found at %s", path)
        return [], []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    competitors: list[SourceConfig] = []
    for item in data.get("competitors", []):
        competitors.append(
            SourceConfig(
                id=item["id"],
                name=item["name"],
                urls=item.get("urls", []),
                js_render=item.get("js_render", False),
                llm_analyze=item.get("llm_analyze", True),
                watch=item.get("watch", []),
            )
        )

    npa_sources: list[dict] = data.get("npa_sources", [])

    logger.info(
        "Loaded sources.yaml: %d competitors, %d NPA sources",
        len(competitors), len(npa_sources),
    )
    return competitors, npa_sources


# ── HTML cleaner ──────────────────────────────────────────────────

def _clean_html(html: str) -> str:
    """Strip navigation/ads, extract meaningful text content."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "iframe", "noscript", "svg"]):
        tag.decompose()

    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(class_=re.compile(r"content|article|post|entry|pricing|features", re.I))
        or soup.find(id=re.compile(r"content|main|article", re.I))
        or soup.body
        or soup
    )

    text = main.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)[:RAW_TEXT_LIMIT]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Fetcher ───────────────────────────────────────────────────────

async def _fetch_url(url: str) -> FetchResult:
    """Fetch a single URL with antibot protection and retry logic.

    Uses httpx.AsyncClient(trust_env=False) — required on Windows proxy setups.
    """
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    last_status: FetchStatus = "error"
    last_error = ""

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            async with httpx.AsyncClient(
                trust_env=False,           # CRITICAL: bypass Windows proxy
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = await client.get(url)

            if response.status_code == 403 or response.status_code == 429:
                logger.warning(
                    "Blocked (%d) fetching %s (attempt %d/%d)",
                    response.status_code, url, attempt, RETRY_COUNT,
                )
                last_status = "blocked"
                # Exponential backoff before retry
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue

            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                logger.warning("Non-HTML content at %s: %s", url, content_type)
                return FetchResult(url=url, status="error", text="", content_hash="")

            cleaned = _clean_html(response.text)
            if len(cleaned) < 50:
                logger.warning("Too little text extracted from %s", url)

            return FetchResult(
                url=url,
                status="ok",
                text=cleaned,
                content_hash=_sha256(cleaned),
            )

        except httpx.TimeoutException:
            logger.warning("Timeout fetching %s (attempt %d/%d)", url, attempt, RETRY_COUNT)
            last_status = "timeout"
            await asyncio.sleep(RETRY_BASE_DELAY * attempt)

        except httpx.HTTPStatusError as e:
            logger.warning("HTTP %d fetching %s", e.response.status_code, url)
            last_status = "error"
            last_error = str(e.response.status_code)
            break  # non-recoverable HTTP error, no retry

        except Exception as e:
            logger.error("Unexpected error fetching %s: %s", url, e)
            last_status = "error"
            last_error = str(e)
            break

    return FetchResult(url=url, status=last_status, text="", content_hash="")


# ── Diff engine ───────────────────────────────────────────────────

def _build_diff(old_text: str, new_text: str) -> str:
    """Build a compact unified diff (max 200 lines) between two texts."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(old_lines, new_lines, lineterm="", n=2)
    )
    return "".join(diff_lines[:200])


def _classify_diff(diff_text: str) -> tuple[bool, Literal["feature", "pricing", "ui", "npa", "minor"]]:
    """Pre-filter: check keywords to decide if LLM analysis is worth running.

    Returns (has_meaningful_changes, change_type).
    change_type is a best-effort guess; LLM will refine in Stage 2.
    """
    lower = diff_text.lower()

    if not any(kw in lower for kw in MEANINGFUL_KEYWORDS):
        return False, "minor"

    # Heuristic type detection from keywords
    pricing_kw = ["цена", "тариф", "стоимость", "price", "pricing", "руб", "₽"]
    feature_kw = ["функци", "проверк", "добавил", "feature", "расширен", "улучшен"]

    if any(kw in lower for kw in pricing_kw):
        return True, "pricing"
    if any(kw in lower for kw in feature_kw):
        return True, "feature"
    return True, "ui"


# ── Main orchestrator ─────────────────────────────────────────────

async def check_competitor(
    source: SourceConfig,
    db: Database,
) -> list[DiffResult]:
    """Fetch all URLs for a competitor, compare with last snapshot.

    Saves new snapshots to DB and records detected changes.
    Returns DiffResult list for URLs where meaningful changes were found.
    """
    results: list[DiffResult] = []

    for url in source.urls:
        # Antibot: random delay between URL fetches
        if url != source.urls[0]:
            delay = random.uniform(MIN_ANTIBOT_DELAY, MAX_ANTIBOT_DELAY)
            logger.debug("Antibot delay %.1fs before %s", delay, url)
            await asyncio.sleep(delay)

        logger.info("[%s] Fetching %s", source.id, url)
        fetch = await _fetch_url(url)

        if fetch.status != "ok":
            # Save failed snapshot for audit trail
            await db.save_snapshot(
                source_id=source.id,
                url=url,
                content_hash="",
                raw_text="",
                fetch_status=fetch.status,
            )
            logger.warning("[%s] Fetch failed (%s): %s", source.id, fetch.status, url)
            continue

        # Compare with last known snapshot
        last = await db.get_last_snapshot(source.id, url)

        # Save current snapshot regardless (keeps history)
        await db.save_snapshot(
            source_id=source.id,
            url=url,
            content_hash=fetch.content_hash,
            raw_text=fetch.text,
            fetch_status="ok",
        )

        if last is None:
            logger.info("[%s] First snapshot for %s — baseline recorded", source.id, url)
            continue  # nothing to diff against yet

        if last["content_hash"] == fetch.content_hash:
            logger.debug("[%s] No changes at %s", source.id, url)
            continue

        # Hash changed — build diff and classify
        old_text = last.get("raw_text") or ""
        diff_text = _build_diff(old_text, fetch.text)
        has_meaningful, change_type = _classify_diff(diff_text)

        logger.info(
            "[%s] Change detected at %s — type=%s meaningful=%s",
            source.id, url, change_type, has_meaningful,
        )

        # Persist change to DB (diff_summary and threat_score added in Stage 2 by LLM)
        await db.save_change(
            source_id=source.id,
            url=url,
            diff_summary=None,  # filled by analyzer_llm in Stage 2
            change_type=change_type,
            threat_score=None,
        )

        if has_meaningful and source.llm_analyze:
            results.append(
                DiffResult(
                    source_id=source.id,
                    url=url,
                    old_text=old_text,
                    new_text=fetch.text,
                    unified_diff=diff_text,
                    has_meaningful_changes=True,
                    change_type=change_type,
                )
            )

    return results


async def run_competitor_check(db: Database) -> list[DiffResult]:
    """Entry point: load sources.yaml and check all competitors.

    Returns all DiffResults with meaningful changes (to be fed into LLM in Stage 2).
    """
    competitors, _ = load_sources()
    all_results: list[DiffResult] = []

    for source in competitors:
        try:
            diffs = await check_competitor(source, db)
            all_results.extend(diffs)
        except Exception as e:
            logger.error("Error checking competitor %s: %s", source.id, e)

    logger.info(
        "Competitor check complete: %d sources, %d meaningful diffs",
        len(competitors), len(all_results),
    )
    return all_results
