# CLAUDE.md — Compliance 152

## Проект

AI-сканер сайтов на соответствие 152-ФЗ «О персональных данных».
Пользователь вводит URL → получает score соответствия, список нарушений,
расчёт штрафов по КоАП и пакет документов для устранения.

## Стек

| Компонент   | Технология                                                   |
|-------------|--------------------------------------------------------------|
| Backend     | Python, FastAPI                                              |
| Краулер     | httpx + BeautifulSoup (статика), Playwright (JS/трекеры)     |
| PDF         | pdfplumber + Yandex Vision OCR (сканы, постранично PNG)      |
| LLM         | OpenRouter API → google/gemini-2.5-pro (контекст 1M токенов) |
| БД          | SQLite (compliance.db)                                       |
| UI          | HTML, CSS, JS, marked.js                                     |
| Экспорт     | DOCX, PDF (кириллица — Times New Roman)                      |

## Архитектура (поток данных)

```
URL → Краулер (httpx/Playwright) → PDF-политики (pdfplumber/OCR)
    → Анализатор (35+ пунктов чеклиста, score, штрафы)
    → LLM (текстовый анализ с рекомендациями)
    → Генератор (12 шаблонов документов, fallback без LLM)
    → Web UI (/check)
```

Автофаллбэк: если SiteScanner даёт плохой результат — автоматически
переключается на PlaywrightCrawler (DEC-002).

## Структура проекта

```
src/
  scanner/      — краулер, детекторы, PDF-экстракторы, трекер-реестр
  analyzer/     — чеклист, score, штрафы КоАП
  generator/    — генерация документов из шаблонов
  llm/          — клиент OpenRouter, кэш, промпты, верификация
  api/          — FastAPI-эндпоинты, оркестрация сканирования
  web/          — маршруты, шаблоны, статика
  export/       — конвертеры DOCX/PDF
  models/       — Pydantic-модели данных
  monitor/      — мониторинг конкурентов и НПА
  notifier/     — Telegram-уведомления
  storage/      — работа с SQLite
  scheduler/    — APScheduler-задачи
  updater/      — обновление данных
  config.py     — конфигурация
  main.py       — точка входа

tests/              — 234+ тестов, fixtures/golden_runs/
knowledge_base/     — чеклисты, шаблоны документов, обновления НПА
config/             — sources.yaml
data/               — БД и результаты сканов
tools/              — run_golden_scan.py (валидационные прогоны)
```

## Правила работы

**Обязательно прочитай `CLAUDE_CODE_RULES.md`** — полный свод правил (15 параграфов).

Критичное:
- `pytest` перед каждым коммитом. Красные тесты не коммитятся.
- Не трогай код вне текущей задачи. Заметил проблему — запиши, не чини.
- Одна задача — один шаг. Больше ~100 строк изменений — согласуй план.
- Файлы в `tests/fixtures/` — эталоны, **никогда не перезаписывать**.
- Ритуал начала сессии (§13): `git branch`, `git status`, `git log -3`.
- Ритуал конца сессии (§14): чеклист из 9 пунктов (0–8).

## Среда

- **ОС:** Windows. Кириллица в путях — только PowerShell, не bash.
- **httpx:** всегда `trust_env=False` (Windows-прокси).
- **Ветка:** fix/test-repair
- **.env:** OPENROUTER_API_KEY, OPENROUTER_MODEL=google/gemini-2.5-pro, BEARER_TOKEN

## Текущий статус

- Тесты: **251 passed / 0 failed**
- Score на el-ed.ru: **64%** (цель ~65% практически достигнута; прогон v5 выполнен 2026-04-17)
- Лимит PDF-текста: **40 000 символов** (был 20k; поднят в сессии 3)
- Валидационная ниша: онлайн-образование (DEC-003)
- Эталон: `tests/fixtures/golden_set_v1.ods` + `GOLDEN_SET_MAPPING.md`

## Сейчас работаем над

1. Подготовка к первым пользователям — стабильность, обработка ошибок

## Документы проекта

| Файл | Что внутри |
|------|------------|
| `ROADMAP.md`            | Дорожная карта: 6 этапов от прототипа до SaaS |
| `PROJECT_PASSPORT.md`   | Полная история проекта, записи всех сессий |
| `NEXT_SESSIONS_PLAN.md` | План ближайших сессий с Claude Code |
| `DECISIONS.md`          | Архитектурные решения (DEC-001..007) |
| `CASES.md`              | Кейсы и баги на реальных сайтах (CASE-001..011) |
| `PATTERNS.md`           | Технические паттерны и уроки |
| `GOLDEN_SET_MAPPING.md` | Маппинг 37 пунктов эталона ↔ 47 CHECK_ID сканера |
| `CLAUDE_CODE_RULES.md`  | Правила работы с Claude Code (15 параграфов) |

## Запуск

```bash
pip install -r requirements.txt
# .env: OPENROUTER_API_KEY, OPENROUTER_MODEL, BEARER_TOKEN
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 9000
# UI: http://127.0.0.1:9000/check
```
