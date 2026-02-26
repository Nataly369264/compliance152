"""Legal verification via web search.

Gathers up-to-date legal context from the internet before document
generation or compliance analysis. Each document type has tailored
search queries to find the most relevant current legal requirements.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from src.llm.client import call_llm
from src.llm.web_tools import format_search_results, web_search

logger = logging.getLogger(__name__)

# ── Search queries per document type ─────────────────────────────

# {year} is replaced at runtime with the current year.
# Each document type gets targeted queries + shared general queries.

SEARCH_QUERIES: dict[str, list[str]] = {
    "privacy_policy": [
        "152-ФЗ политика обработки персональных данных требования {year}",
        "Роскомнадзор политика конфиденциальности обязательные разделы {year}",
        "политика обработки ПДн обязательное содержание актуальные требования",
    ],
    "consent_form": [
        "152-ФЗ согласие обработка персональных данных форма требования {year}",
        "согласие обработка ПДн отдельный документ закон {year}",
        "Роскомнадзор требования согласие субъекта персональных данных",
    ],
    "cookie_policy": [
        "cookie политика персональные данные Россия требования {year}",
        "запрет иностранных cookie сервисов Россия закон {year}",
        "cookie баннер согласие требования 152-ФЗ",
    ],
    "responsible_person_order": [
        "приказ назначение ответственного обработка ПДн 152-ФЗ {year}",
        "статья 22.1 152-ФЗ ответственный обработка персональных данных",
    ],
    "processing_regulation": [
        "положение обработка защита персональных данных организация {year}",
        "152-ФЗ внутреннее положение обработка ПДн обязательные разделы",
        "локализация персональных данных Россия требования {year}",
    ],
    "harm_assessment": [
        "акт оценки вреда субъектам персональных данных приказ РКН 178 {year}",
        "оценка вреда ПДн обязательные требования Роскомнадзор {year}",
    ],
    "nondisclosure_agreement": [
        "обязательство неразглашения персональных данных сотрудники {year}",
        "ответственность разглашение персональных данных штрафы {year}",
    ],
    "incident_instruction": [
        "уведомление Роскомнадзор утечка персональных данных сроки порядок {year}",
        "152-ФЗ инцидент персональные данные реагирование 24 часа {year}",
        "штрафы утечка персональных данных оборотные {year}",
    ],
    "rkn_notification": [
        "уведомление Роскомнадзор обработка ПДн форма порядок подачи {year}",
        "реестр операторов персональных данных РКН регистрация {year}",
    ],
    "consent_withdrawal_form": [
        "отзыв согласия обработка персональных данных форма порядок {year}",
        "право отзыва согласия ПДн 152-ФЗ статья 9",
    ],
    "data_processing_agreement": [
        "договор поручения обработка персональных данных 152-ФЗ {year}",
        "обработчик персональных данных требования договор статья 6",
    ],
    "employee_consent": [
        "согласие сотрудника обработка персональных данных письменная форма {year}",
        "трудовой кодекс обработка ПДн работников согласие {year}",
    ],
}

GENERAL_QUERIES = [
    "152-ФЗ последние изменения поправки {year}",
    "штрафы персональные данные Россия новые размеры {year}",
    "Роскомнадзор проверки персональные данные требования {year}",
]

# ── System prompt for summarizing search results ─────────────────

VERIFICATION_SYSTEM_PROMPT = """Ты — юрист-аналитик, эксперт по российскому законодательству \
о персональных данных (152-ФЗ).

Твоя задача — проанализировать результаты поиска в интернете и выделить ВСЕ актуальные \
требования законодательства, которые относятся к указанному типу документа.

Правила:
1. Укажи ТОЛЬКО проверенные факты из найденных результатов.
2. Для каждого требования укажи:
   - Конкретную статью закона или номер подзаконного акта
   - Дату вступления в силу (если известна)
   - Что именно требуется
3. Если найдены новые штрафы — укажи конкретные суммы.
4. Если найдены изменения, ещё не вступившие в силу — отметь это отдельно.
5. НЕ выдумывай информацию, которой нет в результатах поиска.
6. Отвечай на русском языке, кратко и структурированно.

Формат ответа:
## Актуальные требования для {doc_type}

### Действующие нормы:
- ...

### Новые изменения (если найдены):
- ...

### Актуальные штрафы:
- ...

### Важные разъяснения РКН (если найдены):
- ..."""

VERIFICATION_USER_PROMPT = """Проанализируй результаты поиска и выдели актуальные \
требования законодательства для документа типа «{doc_title}».

Тип документа: {doc_type}
Дата анализа: {current_date}

Результаты поиска по теме документа:
{doc_search_results}

Результаты общего поиска по 152-ФЗ:
{general_search_results}

Выдели ВСЕ актуальные требования, изменения и штрафы, которые нужно учесть \
при генерации или проверке данного документа."""


# ── Main verification function ───────────────────────────────────


async def gather_web_context(
    doc_type: str,
    doc_title: str = "",
    max_results_per_query: int = 3,
) -> str:
    """Gather current legal context from the web for a specific document type.

    Performs targeted web searches, then uses LLM to summarize findings
    into actionable legal requirements.

    Returns a formatted text block ready for injection into the generation prompt.
    Returns empty string if all searches fail.
    """
    year = datetime.now().year

    # Get queries for this doc type (or just general ones)
    doc_queries = SEARCH_QUERIES.get(doc_type, [])
    all_queries = [q.format(year=year) for q in doc_queries]
    general = [q.format(year=year) for q in GENERAL_QUERIES]

    # Run searches in parallel
    logger.info("Running web verification for doc_type=%s (%d queries)", doc_type, len(all_queries) + len(general))

    search_tasks = []
    for q in all_queries:
        search_tasks.append(web_search(q, max_results=max_results_per_query))
    for q in general:
        search_tasks.append(web_search(q, max_results=max_results_per_query))

    all_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # Separate doc-specific and general results
    doc_results: list[dict] = []
    general_results: list[dict] = []

    for i, result in enumerate(all_results):
        if isinstance(result, Exception):
            logger.warning("Search query %d failed: %s", i, result)
            continue
        if not isinstance(result, list):
            continue

        if i < len(all_queries):
            doc_results.extend(result)
        else:
            general_results.extend(result)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique_doc: list[dict] = []
    unique_general: list[dict] = []

    for r in doc_results:
        url = r.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique_doc.append(r)

    for r in general_results:
        url = r.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique_general.append(r)

    total_results = len(unique_doc) + len(unique_general)
    if total_results == 0:
        logger.warning("No search results found for doc_type=%s", doc_type)
        return ""

    logger.info(
        "Web verification for %s: %d doc-specific + %d general results",
        doc_type, len(unique_doc), len(unique_general),
    )

    # Use LLM to summarize and extract actionable requirements
    try:
        summary = await call_llm(
            system_prompt=VERIFICATION_SYSTEM_PROMPT.format(doc_type=doc_title or doc_type),
            user_prompt=VERIFICATION_USER_PROMPT.format(
                doc_type=doc_type,
                doc_title=doc_title or doc_type,
                current_date=datetime.now().strftime("%d.%m.%Y"),
                doc_search_results=format_search_results(unique_doc[:8]),
                general_search_results=format_search_results(unique_general[:5]),
            ),
            max_tokens=4096,
            temperature=0.2,
        )
        return summary

    except Exception as e:
        logger.error("LLM summarization of web context failed: %s", e)
        # Fallback: return raw search results as plain text
        raw = format_search_results(unique_doc[:5] + unique_general[:3])
        return f"[Автоматический анализ не удался. Сырые результаты поиска:]\n\n{raw}"


async def gather_general_legal_context() -> str:
    """Gather general 152-FZ updates from the web (not document-specific).

    Useful for Analyzer to have current context about fines, enforcement, etc.
    """
    year = datetime.now().year
    queries = [q.format(year=year) for q in GENERAL_QUERIES]

    search_tasks = [web_search(q, max_results=3) for q in queries]
    all_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    results: list[dict] = []
    seen: set[str] = set()
    for batch in all_results:
        if isinstance(batch, Exception) or not isinstance(batch, list):
            continue
        for r in batch:
            url = r.get("url", "")
            if url not in seen:
                seen.add(url)
                results.append(r)

    if not results:
        return ""

    try:
        summary = await call_llm(
            system_prompt=(
                "Ты — юрист-аналитик по 152-ФЗ. Проанализируй результаты поиска "
                "и выдели ВСЕ актуальные изменения в законодательстве о персональных данных: "
                "новые штрафы, изменения в требованиях, разъяснения Роскомнадзора, "
                "судебную практику. Отвечай кратко и структурированно на русском."
            ),
            user_prompt=(
                f"Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
                f"Результаты поиска:\n{format_search_results(results[:10])}"
            ),
            max_tokens=3072,
            temperature=0.2,
        )
        return summary
    except Exception as e:
        logger.error("General legal context LLM failed: %s", e)
        return ""
