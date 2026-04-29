"""Legal monitoring: crawls RKN and legal sources for 152-FZ updates.

Uses web search + page fetching to discover new legal changes,
then LLM to analyze their impact on compliance documents.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from src.llm.client import call_llm
from src.llm.utils import parse_llm_json
from src.llm.web_tools import fetch_page, format_search_results, web_search
from src.models.legal_update import LegalUpdate
from src.monitor.competitor import (
    LLM_RATE_PAUSE,
    LLMAnalysis,
    NpaSourceConfig,
    _build_diff,
    _fetch_url,
    load_sources,
)
from src.storage.database import Database

logger = logging.getLogger(__name__)

KB_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base"

# ── Monitoring search queries ────────────────────────────────────

MONITOR_QUERIES = [
    "152-ФЗ персональные данные изменения {year}",
    "Роскомнадзор новые требования персональные данные {year}",
    "штрафы персональные данные Россия закон {year}",
    "законопроект персональные данные Государственная Дума {year}",
    "Роскомнадзор приказ обработка персональных данных {year}",
    "судебная практика персональные данные 13.11 КоАП {year}",
]

# ── NPA keyword pre-filter (base set; sources can add their own) ──────────────

NPA_BASE_KEYWORDS: list[str] = [
    "персональн", "152-фз", "роскомнадзор", "ркн",
    "закон", "поправк", "изменени", "вступает", "вступил",
    "штраф", "обязан", "требован", "приказ", "постановлен",
    "уведомлен", "обработк", "согласи", "оператор",
]

# ── NPA diff LLM prompts ──────────────────────────────────────────

NPA_DIFF_SYSTEM = """\
Ты — юрист-аналитик, эксперт по 152-ФЗ о персональных данных.
Тебе дан unified diff страницы нормативно-правового источника.
Определи:
1. Что изменилось в нормативном содержании (новые статьи, поправки, даты вступления)?
2. Насколько изменение значимо для соответствия 152-ФЗ по шкале 1–5 (5 = критично)?
3. Требует ли изменение немедленных действий от оператора ПДн?

Верни ТОЛЬКО валидный JSON без markdown-оберток:
{
  "summary": "краткое описание изменения (1–3 предложения)",
  "change_type": "npa|minor",
  "threat_score": <1–5>,
  "action_required": true|false,
  "action": "что именно сделать (или пустая строка если action_required=false)"
}

Если diff не содержит значимых нормативных изменений, верни change_type="minor" и threat_score=1."""

NPA_DIFF_USER = """\
Источник НПА: {name}
URL: {url}
Тип источника: {source_type}

Unified diff:
{diff}"""

# ── LLM prompts for web-search based monitoring ──────────────────

MONITOR_ANALYSIS_SYSTEM = """Ты — юрист-аналитик, эксперт по 152-ФЗ о персональных данных.

Проанализируй найденные материалы и определи:
1. Есть ли НОВЫЕ изменения в законодательстве о персональных данных?
2. Для каждого найденного изменения определи:
   - Тип: новый закон, поправка, подзаконный акт, разъяснение, судебная практика
   - Номер и дату документа
   - Краткое описание изменения
   - Какие статьи 152-ФЗ затронуты
   - Дату вступления в силу
   - Какие документы организации нужно обновить
   - Серьёзность: critical / high / medium / low

Отвечай СТРОГО в формате JSON-массива:
[
  {
    "id": "LU-YYYY-NNN",
    "date": "YYYY-MM-DD",
    "effective_date": "YYYY-MM-DD",
    "source": "Название источника",
    "source_url": "URL",
    "title": "Краткое название изменения",
    "summary": "Описание (2-4 предложения)",
    "articles": ["ст. X", "ст. Y"],
    "affected_documents": ["privacy_policy", "consent_form", ...],
    "requirements": ["Требование 1", "Требование 2"],
    "severity": "critical|high|medium|low",
    "category": "law_amendment|regulation|rkn_clarification|court_practice"
  }
]

Допустимые affected_documents: privacy_policy, consent_form, cookie_policy,
responsible_person_order, processing_regulation, harm_assessment,
nondisclosure_agreement, incident_instruction, rkn_notification,
consent_withdrawal_form, data_processing_agreement, employee_consent.

Если новых изменений не найдено, верни пустой массив: []
НЕ выдумывай изменения, которых нет в результатах поиска."""

MONITOR_ANALYSIS_USER = """Дата мониторинга: {date}

Результаты поиска:
{search_results}

Дополнительные материалы с сайта РКН:
{rkn_content}

Проанализируй и выдели ВСЕ новые изменения в законодательстве о ПДн."""


# ── NPA helpers ──────────────────────────────────────────────────

def _count_found_items(text: str) -> int:
    """Count non-empty lines as a proxy for items found on a page."""
    return sum(1 for line in text.split("\n") if line.strip())


def _npa_has_meaningful_change(diff_text: str, source_keywords: list[str]) -> bool:
    """Pre-filter: check if diff contains any NPA-relevant keywords.

    Checks base NPA_BASE_KEYWORDS plus source-specific keywords.
    Returns True if at least one keyword matches.
    """
    lower = diff_text.lower()
    all_keywords = NPA_BASE_KEYWORDS + [kw.lower() for kw in source_keywords]
    return any(kw in lower for kw in all_keywords)


async def _analyze_npa_diff_with_llm(
    source: NpaSourceConfig,
    diff_text: str,
) -> LLMAnalysis | None:
    """Send NPA page diff to LLM and return structured analysis.

    Returns None on failure (caller saves record with threat_score=None).
    """
    user_prompt = NPA_DIFF_USER.format(
        name=source.name,
        url=source.url,
        source_type=source.type,
        diff=diff_text[:4000],
    )

    try:
        raw = await call_llm(
            system_prompt=NPA_DIFF_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=512,
            temperature=0.1,
        )

        data = parse_llm_json(raw)
        if not isinstance(data, dict):
            logger.error("[%s] NPA LLM response is not a dict", source.id)
            return None

        valid_types = {"npa", "minor"}
        change_type = data.get("change_type", "npa")
        if change_type not in valid_types:
            change_type = "npa"

        raw_score = data.get("threat_score", 3)
        try:
            threat_score = max(1, min(5, int(raw_score)))
        except (TypeError, ValueError):
            threat_score = 3

        analysis = LLMAnalysis(
            summary=str(data.get("summary", ""))[:1000],
            change_type=change_type,  # type: ignore[arg-type]
            threat_score=threat_score,
            action_required=bool(data.get("action_required", False)),
            action=str(data.get("action", ""))[:500],
        )
        logger.info(
            "[%s] NPA LLM analysis: type=%s threat=%d action=%s",
            source.id, analysis.change_type, analysis.threat_score, analysis.action_required,
        )
        return analysis

    except Exception as e:
        logger.error("[%s] NPA LLM analysis failed: %s", source.id, e)
        return None


class LegalMonitor:
    """Monitors legal sources for 152-FZ changes."""

    def __init__(self):
        self._seen_hashes: set[str] = set()
        self._load_seen_hashes()

    def _load_seen_hashes(self) -> None:
        """Load hashes of previously seen updates to avoid duplicates."""
        updates_path = KB_DIR / "legal_updates" / "updates.json"
        if updates_path.exists():
            with open(updates_path, encoding="utf-8") as f:
                existing = json.load(f)
            for item in existing:
                key = f"{item.get('source', '')}:{item.get('title', '')}"
                self._seen_hashes.add(hashlib.md5(key.encode()).hexdigest())

    def _is_new(self, source: str, title: str) -> bool:
        """Check if an update is new (not previously seen)."""
        key = f"{source}:{title}"
        h = hashlib.md5(key.encode()).hexdigest()
        return h not in self._seen_hashes

    async def check_for_updates(self) -> list[LegalUpdate]:
        """Run a full monitoring cycle.

        1. Search the web for recent 152-FZ changes
        2. Fetch RKN news pages
        3. Use LLM to analyze and extract structured updates
        4. Filter out duplicates
        5. Return new updates

        Returns list of new LegalUpdate objects.
        """
        year = datetime.now().year
        current_date = datetime.now().strftime("%d.%m.%Y")

        logger.info("Starting legal monitoring cycle for %s", current_date)

        # Step 1: Web search
        all_search_results: list[dict] = []
        for query_template in MONITOR_QUERIES:
            query = query_template.format(year=year)
            try:
                results = await web_search(query, max_results=3)
                all_search_results.extend(results)
            except Exception as e:
                logger.warning("Search failed for query '%s': %s", query[:50], e)

        # Deduplicate search results by URL
        seen_urls: set[str] = set()
        unique_results: list[dict] = []
        for r in all_search_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(r)

        logger.info("Found %d unique search results", len(unique_results))

        # Step 2: Fetch NPA source pages from sources.yaml
        _, npa_sources = load_sources()
        npa_texts: list[str] = []
        for src in npa_sources:
            try:
                text = await fetch_page(src.url, allow_any_domain=True)
                if text and not text.startswith("["):
                    npa_texts.append(f"Источник: {src.name} ({src.url})\n{text[:5000]}")
            except Exception as e:
                logger.warning("Failed to fetch NPA page %s: %s", src.url, e)

        rkn_content = "\n\n---\n\n".join(npa_texts) if npa_texts else "[Не удалось загрузить]"

        # Step 3: LLM analysis
        if not unique_results and not npa_texts:
            logger.warning("No data gathered during monitoring")
            return []

        try:
            raw_response = await call_llm(
                system_prompt=MONITOR_ANALYSIS_SYSTEM,
                user_prompt=MONITOR_ANALYSIS_USER.format(
                    date=current_date,
                    search_results=format_search_results(unique_results[:15]),
                    rkn_content=rkn_content[:8000],
                ),
                max_tokens=4096,
                temperature=0.1,
            )

            # Parse LLM response as JSON
            updates_raw = self._parse_llm_response(raw_response)

        except Exception as e:
            logger.error("LLM analysis failed during monitoring: %s", e)
            return []

        # Step 4: Filter duplicates and validate
        new_updates: list[LegalUpdate] = []
        for item in updates_raw:
            source = item.get("source", "")
            title = item.get("title", "")

            if not title:
                continue

            if not self._is_new(source, title):
                logger.debug("Skipping duplicate update: %s", title[:50])
                continue

            # Generate ID if missing
            if not item.get("id"):
                item["id"] = f"LU-{year}-{str(uuid.uuid4())[:3].upper()}"

            try:
                update = LegalUpdate(**item)
                new_updates.append(update)

                # Mark as seen
                key = f"{source}:{title}"
                self._seen_hashes.add(hashlib.md5(key.encode()).hexdigest())
            except Exception as e:
                logger.warning("Invalid update entry: %s — %s", item.get("title", "?"), e)

        logger.info("Monitoring complete: %d new updates found", len(new_updates))
        return new_updates

    async def check_npa_sources(self, db: Database) -> list[dict]:
        """Check all NPA sources from sources.yaml for page changes.

        For each source:
          1. Fetch URL (antibot + retry via _fetch_url)
          2. Count found_items; emit parse_warning if 0 and last 3 snapshots had content
          3. Save snapshot to DB
          4. If hash unchanged — skip
          5. Build diff + keyword pre-filter
          6. LLM analysis with rate-limit guard (5s between calls)
          7. Save change to DB; fallback saves with threat_score=None, npa_critical=None
          8. npa_critical sources with detected changes → logged as CRITICAL alert

        Returns list of dicts for critical changes (npa_critical=True sources),
        ready for Stage 4 notifier.
        """
        _, npa_sources = load_sources()
        if not npa_sources:
            logger.warning("No NPA sources found in sources.yaml")
            return []

        logger.info("Starting NPA sources check (%d sources)", len(npa_sources))
        critical_alerts: list[dict] = []
        llm_call_count = 0

        for source in npa_sources:
            logger.info("[%s] Fetching %s", source.id, source.url)
            fetch = await _fetch_url(source.url)

            found_items = _count_found_items(fetch.text) if fetch.status == "ok" else 0

            # parse_warning: 0 items AND last 3 snapshots all had content
            if found_items == 0:
                recent = await db.get_recent_snapshots(source.id, source.url, limit=3)
                if len(recent) >= 3 and all(
                    _count_found_items(r.get("raw_text") or "") > 0 for r in recent
                ):
                    logger.warning(
                        "[%s] parse_warning: found_items=0 but last %d snapshots had content "
                        "— possible blocking or structure change at %s",
                        source.id, len(recent), source.url,
                    )

            if fetch.status != "ok":
                await db.save_snapshot(
                    source_id=source.id,
                    url=source.url,
                    content_hash="",
                    raw_text="",
                    fetch_status=fetch.status,
                )
                logger.warning("[%s] Fetch failed (%s): %s", source.id, fetch.status, source.url)
                continue

            last = await db.get_last_snapshot(source.id, source.url)
            await db.save_snapshot(
                source_id=source.id,
                url=source.url,
                content_hash=fetch.content_hash,
                raw_text=fetch.text,
                fetch_status="ok",
            )

            if last is None:
                logger.info("[%s] First NPA snapshot — baseline recorded", source.id)
                continue

            if last["content_hash"] == fetch.content_hash:
                logger.debug("[%s] No changes at %s", source.id, source.url)
                continue

            # Hash changed — build diff and keyword pre-filter
            old_text = last.get("raw_text") or ""
            diff_text = _build_diff(old_text, fetch.text)
            meaningful = _npa_has_meaningful_change(diff_text, source.keywords)

            logger.info(
                "[%s] NPA change detected — meaningful=%s npa_critical=%s",
                source.id, meaningful, source.npa_critical,
            )

            if not meaningful:
                await db.save_change(
                    source_id=source.id,
                    url=source.url,
                    diff_summary=None,
                    change_type="minor",
                    threat_score=None,
                    npa_critical=None,
                )
                continue

            # Rate-limit guard between LLM calls
            if llm_call_count > 0:
                logger.debug("NPA LLM rate-limit pause %.0fs", LLM_RATE_PAUSE)
                await asyncio.sleep(LLM_RATE_PAUSE)

            analysis = await _analyze_npa_diff_with_llm(source, diff_text)
            llm_call_count += 1

            if analysis:
                await db.save_change(
                    source_id=source.id,
                    url=source.url,
                    diff_summary=analysis.summary,
                    change_type=analysis.change_type,
                    threat_score=analysis.threat_score,
                    npa_critical=source.npa_critical,
                )
                if source.npa_critical:
                    alert = {
                        "source_id": source.id,
                        "source_name": source.name,
                        "url": source.url,
                        "summary": analysis.summary,
                        "threat_score": analysis.threat_score,
                        "action_required": analysis.action_required,
                        "action": analysis.action,
                    }
                    critical_alerts.append(alert)
                    logger.critical(
                        "[%s] CRITICAL NPA change detected at %s — threat=%d: %s",
                        source.id, source.url, analysis.threat_score, analysis.summary[:100],
                    )
            else:
                # LLM fallback: save record without score or critical flag
                await db.save_change(
                    source_id=source.id,
                    url=source.url,
                    diff_summary=None,
                    change_type="npa",
                    threat_score=None,
                    npa_critical=None,
                )

        logger.info(
            "NPA check complete: %d sources, %d critical alerts",
            len(npa_sources), len(critical_alerts),
        )
        return critical_alerts

    @staticmethod
    def _parse_llm_response(response: str) -> list[dict]:
        """Extract JSON array from LLM response."""
        data = parse_llm_json(response)
        if isinstance(data, list):
            return data
        return []

    async def save_new_updates(self, updates: list[LegalUpdate]) -> int:
        """Save new updates to the knowledge base JSON file.

        Returns the number of updates saved.
        """
        if not updates:
            return 0

        updates_path = KB_DIR / "legal_updates" / "updates.json"

        # Load existing updates
        existing = []
        if updates_path.exists():
            with open(updates_path, encoding="utf-8") as f:
                existing = json.load(f)

        # Add new updates
        for u in updates:
            existing.append(u.model_dump(mode="json"))

        # Write back
        updates_path.parent.mkdir(parents=True, exist_ok=True)
        with open(updates_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d new legal updates to %s", len(updates), updates_path)
        return len(updates)


async def run_monitoring_cycle() -> list[LegalUpdate]:
    """Run a full monitoring cycle and save results.

    Convenience function for API/CLI use.
    """
    monitor = LegalMonitor()
    new_updates = await monitor.check_for_updates()

    if new_updates:
        await monitor.save_new_updates(new_updates)

    return new_updates
