# Handoff — Competitor Intelligence Monitor
Дата: 06.03.2026

## Статус
Этапы 1–6 завершены.

## Ключевые файлы
- config/sources.yaml — конфигурация источников + scheduler cron-строки
- src/monitor/competitor.py — run_competitor_check(db)
- src/monitor/monitor.py — LegalMonitor.check_npa_sources(db) → list[dict] critical_alerts
- src/monitor/reporter.py — DigestReporter: build_digest(npa, competitors) + send(notifier)
- src/notifier/telegram.py — TelegramNotifier: send_critical_alert + send_digest
- src/storage/database.py — list_pending_changes, mark_changes_digested, save_digest
- PROGRESS.md — статус всех этапов

## Интерфейс DigestReporter (Stage 5 → Stage 6)
reporter = DigestReporter()
digest = reporter.build_digest(npa_changes, competitor_changes)  # → str | None
await reporter.send(notifier)           # отправляет, если digest != None

# Типичный flow в Stage 6:
all_pending = await db.list_pending_changes()
npa     = [c for c in all_pending if c["change_type"] == "npa" or c["npa_critical"]]
comps   = [c for c in all_pending if c not in npa]
digest  = reporter.build_digest(npa, comps)
if digest:
    await reporter.send(notifier)
    await db.mark_changes_digested([c["id"] for c in all_pending])
    await db.save_digest(period_start, period_end, digest)

## Важные технические решения
- httpx.AsyncClient(trust_env=False) — обязательно везде
- raw_text лимит 10 КБ (в save_snapshot)
- rate-limit guard: sleep(5s) между LLM-запросами
- Fallback при LLM-сбое: запись сохраняется с threat_score=None

## Anti-patterns (не делать)
- НЕ использовать константу RKN_URLS из monitor.py — источник истины только sources.yaml (секция npa_sources)
- НЕ запускать LLM-вызовы параллельно — только sequential queue через analyze_diffs()

## Patterns (зафиксированные решения)

### Scheduler
- Единственный `AsyncIOScheduler` создаётся через `create_scheduler()` в `src/scheduler/jobs.py`
- Cron-выражения читаются из `sources.yaml::scheduler` через `CronTrigger.from_crontab()`
- `AsyncIOScheduler` inline нигде не создавать — только через `create_scheduler()`
- В `lifespan` можно добавлять дополнительные джобы к уже созданному scheduler, но не создавать второй

### Bearer Auth
- Middleware в `server.py` покрывает весь `/api/v1/*` автоматически
- Новые эндпоинты под `/api/v1/` не требуют отдельного `Depends()` или декоратора
- Принимает любой непустой токен (`Authorization: Bearer <anything>`) — TODO: реальная проверка
