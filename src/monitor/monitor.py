"""Legal monitoring: crawls RKN and legal sources for 152-FZ updates.

Uses web search + page fetching to discover new legal changes,
then LLM to analyze their impact on compliance documents.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from src.llm.client import call_llm
from src.llm.web_tools import fetch_page, format_search_results, web_search
from src.models.legal_update import LegalUpdate

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

# ── RKN specific URLs to monitor ─────────────────────────────────

RKN_URLS = [
    "https://rkn.gov.ru/news/rsoc/",
    "https://pd.rkn.gov.ru/press-service/news/",
]

# ── LLM prompts for analyzing legal changes ──────────────────────

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

        # Step 2: Fetch RKN pages
        rkn_texts: list[str] = []
        for url in RKN_URLS:
            try:
                text = await fetch_page(url, allow_any_domain=True)
                if text and not text.startswith("["):
                    rkn_texts.append(f"Источник: {url}\n{text[:5000]}")
            except Exception as e:
                logger.warning("Failed to fetch RKN page %s: %s", url, e)

        rkn_content = "\n\n---\n\n".join(rkn_texts) if rkn_texts else "[Не удалось загрузить]"

        # Step 3: LLM analysis
        if not unique_results and not rkn_texts:
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

    @staticmethod
    def _parse_llm_response(response: str) -> list[dict]:
        """Extract JSON array from LLM response."""
        # Try direct JSON parse
        text = response.strip()

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError:
            # Try to find JSON array in the text
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass

            logger.error("Failed to parse LLM response as JSON")
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
