"""Competitor Intelligence Monitor — fetcher, diff engine, LLM analysis.

Stage 1: page fetching with antibot protection, SHA-256 deduplication,
         diff building and keyword pre-filter.
Stage 2: sequential LLM analysis queue with rate-limit guard, threat_score,
         JSON-structured analysis saved back to competitor_changes table.
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

from src.llm.client import call_llm
from src.llm.utils import parse_llm_json
from src.storage.database import Database

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────

RAW_TEXT_LIMIT = 10_240          # max bytes stored in DB per snapshot
FETCH_TIMEOUT = 25               # seconds per request
RETRY_COUNT = 3                  # attempts per URL
RETRY_BASE_DELAY = 2.0           # seconds, doubled each retry (exp backoff)
MIN_ANTIBOT_DELAY = 3.0          # seconds between requests (random)
MAX_ANTIBOT_DELAY = 8.0
LLM_RATE_PAUSE = 5.0             # seconds between LLM calls (rate-limit guard)

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
    """Parsed entry from sources.yaml (competitors section)."""
    id: str
    name: str
    urls: list[str]
    js_render: bool = False
    llm_analyze: bool = True
    watch: list[str] = field(default_factory=list)


@dataclass
class NpaSourceConfig:
    """Parsed entry from sources.yaml (npa_sources section)."""
    id: str
    name: str
    url: str
    type: str           # html_list | html_hash
    npa_critical: bool = False
    keywords: list[str] = field(default_factory=list)


@dataclass
class LLMAnalysis:
    """Structured result from LLM diff analysis."""
    summary: str
    change_type: Literal["feature", "pricing", "ui", "npa", "minor"]
    threat_score: int          # 1–5
    action_required: bool
    action: str


# ── sources.yaml loader ───────────────────────────────────────────

def load_sources(path: Path = SOURCES_PATH) -> tuple[list[SourceConfig], list[NpaSourceConfig]]:
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

    npa_sources: list[NpaSourceConfig] = []
    for item in data.get("npa_sources", []):
        npa_sources.append(
            NpaSourceConfig(
                id=item["id"],
                name=item["name"],
                url=item["url"],
                type=item.get("type", "html_hash"),
                npa_critical=item.get("npa_critical", False),
                keywords=item.get("keywords", []),
            )
        )

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


# ── LLM prompts ───────────────────────────────────────────────────

_COMPETITOR_SYSTEM = """\
Ты — аналитик продукта в области compliance 152-ФЗ.
Тебе дан unified diff изменений страницы конкурента.
Определи:
1. Что изменилось (новые функции, цены, формулировки, UI)?
2. Уровень угрозы для нашего продукта по шкале 1–5 (5 = критично).
3. Требует ли изменение наших действий?

Верни ТОЛЬКО валидный JSON без markdown-оберток:
{
  "summary": "краткое описание изменения (1–3 предложения)",
  "change_type": "feature|pricing|ui|minor",
  "threat_score": <1–5>,
  "action_required": true|false,
  "action": "что именно сделать (или пустая строка если action_required=false)"
}"""

_COMPETITOR_USER = """\
Конкурент: {name}
URL: {url}

Unified diff:
{diff}"""


# ── LLM parser ────────────────────────────────────────────────────

def _parse_llm_analysis(response: str) -> LLMAnalysis | None:
    """Extract and validate JSON from LLM response."""
    data = parse_llm_json(response)
    if not isinstance(data, dict):
        return None

    # Validate and normalise fields
    valid_types = {"feature", "pricing", "ui", "npa", "minor"}
    change_type = data.get("change_type", "ui")
    if change_type not in valid_types:
        change_type = "ui"

    raw_score = data.get("threat_score", 3)
    try:
        threat_score = max(1, min(5, int(raw_score)))
    except (TypeError, ValueError):
        threat_score = 3

    return LLMAnalysis(
        summary=str(data.get("summary", ""))[:1000],
        change_type=change_type,  # type: ignore[arg-type]
        threat_score=threat_score,
        action_required=bool(data.get("action_required", False)),
        action=str(data.get("action", ""))[:500],
    )


# ── LLM analyzer ─────────────────────────────────────────────────

async def _analyze_diff_with_llm(
    diff: DiffResult,
    source_name: str,
) -> LLMAnalysis | None:
    """Send a single diff to LLM and return structured analysis.

    Returns None on failure (caller decides how to handle).
    """
    # Truncate diff to avoid excessive tokens (~4000 chars ≈ ~1000 tokens)
    diff_text = diff.unified_diff[:4000]

    user_prompt = _COMPETITOR_USER.format(
        name=source_name,
        url=diff.url,
        diff=diff_text,
    )

    try:
        raw = await call_llm(
            system_prompt=_COMPETITOR_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=512,
            temperature=0.1,
        )
        analysis = _parse_llm_analysis(raw)
        if analysis:
            logger.info(
                "[%s] LLM analysis: type=%s threat=%d action=%s",
                diff.source_id, analysis.change_type,
                analysis.threat_score, analysis.action_required,
            )
        return analysis

    except Exception as e:
        logger.error("[%s] LLM analysis failed for %s: %s", diff.source_id, diff.url, e)
        return None


async def analyze_diffs(
    diffs: list[DiffResult],
    db: Database,
    source_map: dict[str, str],  # source_id → source_name
) -> int:
    """Run LLM analysis sequentially with rate-limit guard.

    Processes only diffs with has_meaningful_changes=True.
    Saves each result to competitor_changes table.
    Pauses LLM_RATE_PAUSE seconds between calls to avoid exhausting free-tier limits.

    Returns count of successfully analysed diffs.
    """
    meaningful = [d for d in diffs if d.has_meaningful_changes]
    if not meaningful:
        logger.info("No meaningful diffs to analyse")
        return 0

    logger.info("Starting LLM analysis for %d diffs (rate pause %.0fs)", len(meaningful), LLM_RATE_PAUSE)
    analysed = 0

    for i, diff in enumerate(meaningful):
        if i > 0:
            logger.debug("LLM rate-limit pause %.0fs", LLM_RATE_PAUSE)
            await asyncio.sleep(LLM_RATE_PAUSE)

        name = source_map.get(diff.source_id, diff.source_id)
        analysis = await _analyze_diff_with_llm(diff, name)

        if analysis:
            await db.save_change(
                source_id=diff.source_id,
                url=diff.url,
                diff_summary=analysis.summary,
                change_type=analysis.change_type,
                threat_score=analysis.threat_score,
            )
            analysed += 1
        else:
            # LLM failed — save with heuristic type and no score so record isn't lost
            await db.save_change(
                source_id=diff.source_id,
                url=diff.url,
                diff_summary=None,
                change_type=diff.change_type,
                threat_score=None,
            )

    logger.info("LLM analysis complete: %d/%d successful", analysed, len(meaningful))
    return analysed


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

        if not has_meaningful or not source.llm_analyze:
            # Minor change or LLM disabled — save immediately, no LLM needed
            await db.save_change(
                source_id=source.id,
                url=url,
                diff_summary=None,
                change_type=change_type,
                threat_score=None,
            )
        else:
            # Meaningful change with LLM enabled — defer to analyze_diffs()
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


async def run_competitor_check(db: Database) -> int:
    """Entry point: load sources.yaml, check all competitors, run LLM analysis.

    Full pipeline:
      1. Fetch all competitor URLs (antibot + retry)
      2. SHA-256 compare with last snapshot
      3. Diff + keyword pre-filter
      4. Minor changes → saved immediately
      5. Meaningful changes → sequential LLM analysis with rate-limit guard

    Returns count of LLM-analysed changes.
    """
    competitors, _ = load_sources()
    all_diffs: list[DiffResult] = []
    source_map: dict[str, str] = {s.id: s.name for s in competitors}

    for source in competitors:
        try:
            diffs = await check_competitor(source, db)
            all_diffs.extend(diffs)
        except Exception as e:
            logger.error("Error checking competitor %s: %s", source.id, e)

    logger.info(
        "Fetch phase complete: %d sources, %d diffs queued for LLM",
        len(competitors), len(all_diffs),
    )

    return await analyze_diffs(all_diffs, db, source_map)
