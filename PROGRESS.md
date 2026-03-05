# Project Progress

## Competitor Intelligence Monitor

- [x] Этап 1 — sources.yaml, fetcher, diff, DB migration (05.03.2026) — коммит 7cb05a9
- [x] Этап 2 — LLM-анализ, очередь, rate-limit guard, threat_score (05.03.2026) — коммит af40fd5
- [x] Пре-этап 3 — parse_llm_json в utils.py, NpaSourceConfig, обновление PROGRESS/HANDOFF (05.03.2026)
- [x] Этап 3 — check_npa_sources, parse_warning, NPA diff LLM, keywords pre-filter (05.03.2026)
- [x] Этап 4 — src/notifier/telegram.py: TelegramNotifier, retry, graceful skip (05.03.2026)
- [x] Этап 5 — src/monitor/reporter.py: DigestReporter, build_digest, send (05.03.2026)
- [ ] Этап 6 — src/scheduler/jobs.py, единый APScheduler, API-эндпоинты с Bearer auth
- [ ] Этап 7 — tests/test_competitor.py
