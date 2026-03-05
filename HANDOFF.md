# Handoff — Competitor Intelligence Monitor
Дата: 05.03.2026

## Статус
Этап 1 (7cb05a9) и Этап 2 (af40fd5) завершены.

## Следующий шаг — Этап 3
Расширить LegalMonitor в src/monitor/monitor.py:
- Добавить парсинг URL из config/sources.yaml (секция npa_sources)
- Флаг npa_critical → мгновенный вызов notifier при изменении
- parse_warning: алерт если found_items == 0
- НЕ пересоздавать логику — только расширять существующий класс

## Ключевые файлы
- config/sources.yaml — конфигурация источников
- src/monitor/competitor.py — fetcher, diff, LLM-анализ
- src/monitor/monitor.py — LegalMonitor (расширяем здесь)
- src/storage/database.py — 3 новые таблицы + 8 методов
- src/llm/client.py — LLM-инфраструктура (переиспользуем)
- PROGRESS.md — статус всех этапов

## Важные технические решения
- httpx.AsyncClient(trust_env=False) — обязательно везде
- raw_text лимит 10 КБ (в save_snapshot)
- rate-limit guard: sleep(5s) между LLM-запросами
- Fallback при LLM-сбое: запись сохраняется с threat_score=None
