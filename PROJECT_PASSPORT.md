
PROJECT PASSPORT — Compliance 152-ФЗ

**Последнее обновление:** 2026-04-05, сессия тестирования и исправлений (задачи A, B, C)

Суть проекта
AI-сканер сайтов на соответствие требованиям 152-ФЗ «О персональных данных». Автоматически проверяет сайт, выявляет нарушения, рассчитывает штрафы по КоАП и генерирует пакет документов для устранения нарушений.

Целевая аудитория
Компании, которые собирают персональные данные на сайте и хотят проверить соответствие 152-ФЗ без дорогостоящего юридического аудита.

Ценностное предложение
За 1–2 минуты получить: score соответствия, список конкретных нарушений, расчёт потенциального штрафа и готовые документы для исправления ситуации.

Технический стек
| Компонент   | Технология                                                        |
| ----------- | ----------------------------------------------------------------- |
| Backend     | Python, FastAPI                                                   |
| Краулер     | httpx + BeautifulSoup (статика), Playwright (JS/трекеры)          |
| PDF         | pdfplumber (извлечение текста из PDF-политик)                     |
| LLM         | OpenRouter API (google/gemini-2.5-pro, контекст 1M токенов)       |
| База данных | SQLite (compliance.db)                                            |
| Web UI      | HTML, CSS, JS, marked.js                                          |
| Планировщик | APScheduler (реализован в модуле Competitor Intelligence Monitor) |
| Авторизация | Bearer token middleware                                           |
| Экспорт     | DOCX, PDF (кириллица — Times New Roman)                           |

Архитектура
URL сайта
    ↓
Краулер (crawler.py / playwright_crawler.py)
    — статический обход: httpx + BeautifulSoup
    — JS-обход: Playwright (трекеры, динамические баннеры)
    — PDF-политики: pdfplumber
    — ищет формы, скрипты, cookie-баннеры, политику конфиденциальности
    — собирает всех кандидатов политики, выбирает лучшую (_select_best_policy)
    ↓
Анализатор (analyzer.py) — проверяет 35+ пунктов чеклиста,
    считает score и штрафы по КоАП
    ↓
LLM (OpenRouter / gemini-2.5-pro) — формирует текстовый анализ с рекомендациями
    ↓
Генератор (generator.py) — создаёт пакет документов из 12 шаблонов
    (fallback: шаблонный документ с дисклеймером, если LLM недоступен)
    ↓
Web UI (/check) — отображает результат пользователю


Текущий статус (март 2026)
✅ Готово
Краулер — многостраничный обход, дедупликация URL
  → статический (httpx + BeautifulSoup) + Playwright для JS-трекеров
  → умный выбор политики: collect-all + _select_best_policy (длина текста + URL-приоритет)
  → обработка PDF-политик через pdfplumber

Анализатор — 35+ пунктов чеклиста, score, штрафы КоАП
  → TRACKER_001: трекер без упоминания в политике
  → TRACKER_002: трекер без согласия пользователя

Провенанс документа (ScanMetadata) — text_hash, fetched_at, content_length, text_truncated

LLM-анализ — OpenRouter, модель google/gemini-2.5-pro (контекст 1M токенов)

12 шаблонов документов — все созданы, протестированы, с LLM-fallback на шаблонный документ

Web UI — страница /check с score, нарушениями, штрафом, текстом LLM

Bearer token middleware — защита /api/v1/*

Экспорт DOCX и PDF с корректной кириллицей

Competitor Intelligence Monitor — реализован полностью (7 этапов):
  мониторинг конкурентов + НПА, Telegram-уведомления, APScheduler, 14 тестов

CONSENT_CHECK (Этап 5) — проверки согласия по ст. 9 152-ФЗ — ✅ выполнено
  → CONSENT_001–005 в website_checklist.json, _check_consent() в analyzer.py
  → 8 тестов (tests/test_consent_checks.py), 173 passed / 0 failed

Этап 6 — рефакторинг краулера (задачи 6.1–6.4) — ✅ выполнено
  → 6.1: smoke-тесты SiteScanner (16 тестов, мок httpx без реальных запросов)
  → 6.2: общий код вынесен в src/scanner/utils.py
         (normalize_url, is_same_domain, should_skip, SKIP_EXTENSIONS, FALLBACK_PRIVACY_PATHS)
  → 6.3: PlaywrightCrawler — перехват сетевых запросов через page.on('request'),
         analytics_before_consent заполняется реальными данными через tracker_registry
  → 6.4: детекторы JS-контента — паттерны OneTrust, CookieYes в detect_cookie_banner();
         Cookiebot и Tilda/Bitrix-формы подтверждены тестами;
         исправлен баг: отсутствовал import re в playwright_crawler.py

Репозиторий на GitHub (приватный), ветка fix/test-repair, 199 тестов passed / 0 failed

### 2026-04-05 — Сессия тестирования на реальных сайтах + исправления
- Документация: создан DECISIONS.md, добавлен §12 в CLAUDE_CODE_RULES.md
- Тестирование SiteScanner + PlaywrightCrawler на 5 сайтах (wildberries, tinkoff, ozon, mos.ru, dns-shop)
- Зафиксированы CASE-001..005 в CASES.md, решения DEC-001/DEC-002 в DECISIONS.md
- **(A)** PlaywrightCrawler UA → `Chrome/120` (`playwright_crawler.py`)
- **(B)** Ошибки логируются с `type(e).__name__` в обоих краулерах (3 места)
- **(C = 6.5)** Валидация найденной политики (`_select_best_policy`): `is_valid_policy_text()` в `utils.py`;
  WAF/challenge-страницы больше не дают ложный `pp.found=True`
- Тесты: 199 passed / 0 failed

### 2026-04-05 — Golden set для валидации сканера
- Выбрана ниша валидации: онлайн-образование (DEC-003)
- Создан `GOLDEN_SET_MAPPING.md` — сопоставление 37 пунктов эталона с 47 CHECK_ID чеклиста сканера
- Эталонная таблица `tests/fixtures/golden_set_v1.ods` — колонка el-ed.ru заполнена на 35/37
- Следующий шаг: первый прогон сканера на el-ed.ru со сравнением результата с эталоном

### 2026-04-05 — Git-гигиена: переписан §13 CLAUDE_CODE_RULES.md
- Обнаружен и закрыт разрыв между паспортом и Git: ~800 строк кода Этапов 5 и 6 
  лежали на диске без коммитов 2–3 сессии (коммиты `42a50c4`, `b11ab78`, `49d1fc2`)
- Переписан §13 `CLAUDE_CODE_RULES.md`: ритуалы начала и конца сессии, pytest 
  перед коммитом, правило «готово = в origin»
- Добавлен автозапуск ритуала начала сессии в блок «Как использовать этот файл»
- Урок зафиксирован в `PATTERNS.md`

🔲 В работе / Следующие шаги
Этап 6 (продолжение):
  → Синхронизация _extract_privacy_policy между краулерами
     (truncation 20k vs 100k, отсутствуют text_hash/fetched_at/content_length)
  → Ускорение тестов: asyncio.sleep(2) в _crawl() увеличивает время прогона (~15 сек)

Краулер — следующие задачи (из сессии 2026-04-05):
  → (D) Корректный учёт 4xx-ответов: в pages_scanned не засчитывать, в errors фиксировать
  → (E) Автоматический fallback SiteScanner → PlaywrightCrawler при плохом результате (DEC-002)
  → (F) Stealth-режим Playwright: --disable-blink-features=AutomationControlled,
         скрытие navigator.webdriver — для сайтов с bot fingerprinting (CASE-002)

wappalyzer-next — интеграция для определения технологий по заголовкам ответа
  → план зафиксирован в docs_scanner_logic.md

Явная обработка 403/429 → scan_limitations
  (сейчас попадают в errors[], нужно определять DDoS-Guard/Cloudflare)

Полировка UI (мобильная адаптация, цвета риска, центровка score-круга)

Monitor/Updater — уведомления при изменении score

Запуск проекта
# Установка зависимостей
pip install -r requirements.txt

# Настройка .env (создать вручную!)
OPENROUTER_API_KEY=твой_ключ
OPENROUTER_MODEL=google/gemini-2.5-pro
BEARER_TOKEN=любой_набор_символов

# Запуск сервера
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 9000

# Web UI
http://127.0.0.1:9000/check

Репозиторий
https://github.com/Nataly369264/compliance152 (приватный)

Команда

Дмитрий — первоначальный прототип

Наталья — со-разработка, тестирование

Claude Code — разработка и исправление багов

Perplexity — стратегия сессий, контроль контекста

Известные особенности
Windows-прокси: httpx.Client(trust_env=False) — обязательно

Папка проекта содержит кириллицу в имени — bash не работает, только PowerShell или терминал VS Code

SESSION_*.md исключены из Git — хранятся локально

