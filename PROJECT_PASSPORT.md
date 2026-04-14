
PROJECT PASSPORT — Compliance 152-ФЗ

**Последнее обновление:** 2026-04-14, техническая правка — ускорение test_truncates_long_text (~6s → ~1s), 219 passed

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
| PDF         | pdfplumber (machine-generated PDFs) + Yandex Vision OCR (scanned PDFs) |
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
    — PDF-политики: pdfplumber → Yandex Vision OCR (постраничный PNG, каскад)
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

Рабочий процесс.

### Правило handover между сессиями чата (Claude в claude.ai)

В конце каждой сессии чата Claude генерирует файл 
SESSION_HANDOVER_<YYYY-MM-DD>.md в формате:

- Краткий контекст сессии (что обсуждали, что решали)
- Принятые решения (кратко, со ссылками на DEC-NNN если применимо)
- Открытые вопросы / отложенные задачи
- Что принести в следующую сессию (явный список файлов)
- Любые важные нюансы для «следующей меня» — то, что не очевидно 
  из коммитов и проектных документов

Триггер: Claude генерирует handover самостоятельно, когда 
становится понятно, что сессия завершается (пользователь 
прощается, или явно говорит «закругляемся», или контекст 
подходит к пределу). Не нужно отдельно просить.

Файл сохраняется локально вместе с другими документами проекта 
(не в Git, по аналогии с SESSION_*.md).

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

### 2026-04-10 — Сессия 1.1: Bug A фикс + pdf_extractors + manual_review_needed

- **Bug A устранён:** PDF-ссылки на политики (`policy.pdf` и подобные) больше
  не фильтруются `should_skip()` — проходят через оба краулера
- **Новый модуль `src/scanner/pdf_extractors.py`:** каскад
  `PdfplumberExtractor → YandexVisionExtractor (stub) → failed`
- **Новый статус `manual_review_needed`:** когда политика найдена по URL, но текст
  PDF не извлекается — content-проверки POLICY_003..016 не идут в знаменатель score
- **`ScanMetadata.extraction_method`** — фиксирует метод извлечения текста (внутренний)
- **DEC-004:** выбрана стратегия Yandex Vision OCR для сессии 1.2
- **DEC-005:** технические детали (краулер, OCR-метод) не выводятся в пользовательский отчёт
- Тесты: 199 → 204 passed
- **Следующий шаг — Сессия 1.2:** реализация `YandexVisionExtractor`

### 2026-04-06 — Первый валидационный прогон на el-ed.ru
- Создан `tools/run_golden_scan.py` — автономный runner (SiteScanner + ComplianceAnalyzer, полный LLM)
- Прогон сохранён в `tests/fixtures/golden_runs/el-ed_2026-04-06.json` (сырые данные)
- Сравнительная таблица: `tests/fixtures/golden_runs/el-ed_2026-04-06_diff.md`
- **Score сканера: 21%** (ожидание по эталону: ~65%)
- Главная находка: `pp.found=false` — сканер не нашёл политику el-ed.ru за 50 страниц,
  что каскадом обнуляет 17/34 пунктов golden set (POLICY_001..017, TRACKER_001)
- Реальных расхождений без каскада: 4 (FORM_001 охват, TECH_003/TECH_005 пропуск трекеров, FORM_006/COOKIE_003 WARNING вместо FAIL)
- Добавлен CASE-006: LLM-клиент использует openrouter/free вместо gemini-2.5-pro (требует диагностики)
- Тесты: 199 passed / 0 failed

### 2026-04-05 — Git-гигиена: переписан §13 CLAUDE_CODE_RULES.md
- Обнаружен и закрыт разрыв между паспортом и Git: ~800 строк кода Этапов 5 и 6 
  лежали на диске без коммитов 2–3 сессии (коммиты `42a50c4`, `b11ab78`, `49d1fc2`)
- Переписан §13 `CLAUDE_CODE_RULES.md`: ритуалы начала и конца сессии, pytest 
  перед коммитом, правило «готово = в origin»
- Добавлен автозапуск ритуала начала сессии в блок «Как использовать этот файл»
- Урок зафиксирован в `PATTERNS.md`

### 2026-04-11 — Сессия 1.2: реализация YandexVisionExtractor

- **`YandexVisionExtractor` реализован** в `src/scanner/pdf_extractors.py`:
  HTTP-клиент к `ocr.api.cloud.yandex.net/ocr/v1/recognizeFile`, 2 ретрая
  с экспоненциальной задержкой, fallback в `manual_review_needed` при ошибке
- **`.env.example` обновлён:** добавлены `YANDEX_VISION_API_KEY` и `YANDEX_FOLDER_ID`
- **`tools/run_golden_scan.py`:** добавлен `load_dotenv()` с явным путём к `.env`
- **5 новых тестов** `YandexVisionExtractor` (happy path, retry, 5xx, empty, credentials)
- **CASE-008 открыт:** Vision OCR возвращает HTTP 404 на el-ed.ru — предположительно
  неверный `YANDEX_FOLDER_ID` или сервис не активирован в Yandex Cloud Console.
  Код реализован корректно, блокер — конфигурация облака.
- Тесты: 204 → 209 passed, 5 коммитов в origin (`d032c26`..`db612dd`)
- **CASE-008 открыт** (закрыт в сессии 2026-04-12 ниже)

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (обновлено — 1.2 в статус «требует доработки по CASE-008»), CASES (CASE-008). DECISIONS, PATTERNS, GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-12 — Сессия 1.2 (финал): Yandex OCR постранично, CASE-008 закрыт

- **Диагноз CASE-008 уточнён:** HTTP 404 → при смене URL на `recognizeText` стал HTTP 400 с
  телом `"Request pages 14, service page limit is 1"` — API принимает максимум 1 страницу за запрос
- **URL исправлен:** `recognizeFile` → `recognizeText` (правильный endpoint)
- **Архитектурный рефакторинг `YandexVisionExtractor`:** PDF разбивается постранично через
  `pdfplumber.to_image()`, каждая страница отправляется как PNG (resolution=200)
- **Константы:** `MAX_PAGES=8`, `_PAGE_DELAY=0.3s` между страницами, `_RETRY_ATTEMPTS_NETWORK=3`
  (сетевые ошибки) vs `_RETRY_ATTEMPTS=2` (HTTP 5xx) — разделённые лимиты retry
- **Проверено на реальном PDF el-ed.ru** (`policy.pdf`, 14 стр.): 20 000 символов извлечено,
  `error=None`, `method="yandex_vision"`. Текст начинается: «Политика обработки персональных
  данных. г. Иркутск. редакция от 15.05.2025 г.»
- **DEC-006** добавлен: страничный PNG-подход для обхода лимита Yandex OCR
- **CASE-008 закрыт:** причина — API page limit 1, решение — постраничный PNG
- Тесты: 209 → 210 passed, 1 коммит в origin (`559dfa3`)

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (1.2 → ✅ Выполнено), DECISIONS (DEC-006), CASES (CASE-008 закрыт), PATTERNS (паттерн PDF→PNG для OCR). GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-12 — Сессия 1.3: Дорожка А — внешняя память проекта через MCP filesystem

- Принято DEC-007: внешняя память через MCP filesystem без Obsidian (вариант 2 из `TRACK_A_PLAN.md`). Решает главную боль проекта — потерю лимитов на ручной перенос контекста между чатами.
- На машине пользователя настроен MCP-сервер `compliance152` через `@modelcontextprotocol/server-filesystem` (запуск через `npx -y`). Конфиг положен по нестандартному пути Store-версии Claude Desktop: `C:\Users\ЗС\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`. Существующие `preferences` в конфиге сохранены.
- Функциональный тест пройден: Claude Desktop видит корень проекта `C:\Projects\compliance152` и успешно вызывает `list_directory`. Ручное прикладывание файлов в новые чаты больше не требуется.
- Попытка перехода со Store-версии Claude Desktop на standalone не удалась: APPX-пакет с `SignatureKind: Developer` не удаляется стандартными средствами. Обход найден, детали в CASE-009. Удаление Store-версии — не блокер, переложено в backlog.
- В корень проекта добавлены `TRACK_A_PLAN.md` и `NEW_SESSION_BRIEFING.md` — полный контекст задачи и обоснование выбора варианта 2.
- Известное ограничение Claude Code, вскрытое в этой сессии: падает при попытке выполнить `Get-Process claude | Stop-Process`. Обход — процессы Claude Desktop завершает пользователь вручную в PowerShell. Зафиксировано в CASE-009.

**Статус дорожек:** А ✅ закрыта. Б (корпус нормативки) и В (аудит кода сканера) — по прежнему порядку, после обкатки MCP на одной-двух реальных сессиях.

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (сессия 1.3 добавлена как ✅ Выполнено), DECISIONS (DEC-007), CASES (CASE-009), PATTERNS (паттерн «Запись JSON-конфига с Windows-путями из PowerShell»). GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-13 — Сессия 2.0: валидация OCR + фиксы CASE-007 и CASE-010

- **Валидационный прогон v1** на el-ed.ru показал `pp.found=False` несмотря на успешное OCR (23 894 символа извлечено). Причина — два последовательных бага.
- **CASE-010 (открыт и закрыт):** `_is_russian()` использовала regex `[а-яА-ЯёЁ]{20+}` — не работает на OCR-тексте где слова разделены пробелами. Фикс: перешла на подсчёт доли кириллицы (≥50% букв + ≥50 символов). 5 тестов. Тесты: 210 → 215 passed.
- **CASE-007 (закрыт):** `_select_best_policy` — валидация до выбора победителя. 1 тест. Тесты: 215 → 216 passed.
- Все 4 коммита в origin. На начало следующей сессии Claude Code: прогон v3 на el-ed.ru — ожидаем `pp.found=True` и score ~65%.

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (сессия 2.0 добавлена), CASES (CASE-007 закрыт, CASE-010 открыт+закрыт). DECISIONS, PATTERNS, GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-13 — Прогон v3 на el-ed.ru

- `pp.found=True` ✅, score 21% → **39%**, каскад снят с 16 строк → 2, совпало 12/34.
- Остаток: `_select_best_policy` выбирает `reviews-policy.pdf` вместо `policy.pdf` — POLICY_003–016 остаются в `manual_review_needed`. CASE-011 открыт, сессия 2.0 не закрыта.

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (итог 2.1 + сессия 2.2 добавлена), CASES (CASE-011 закрыт), RULES (§15 — не записан, планируется в сессии 2.2). DECISIONS, PATTERNS, GOLDEN_SET_MAPPING — не трогались.

### 2026-04-13 — Сессия 2.2: финальный прогон v4 + §15 в RULES

- `pp.found=True` ✅, `pp.url=policy.pdf#page=4` (верная политика), `content_length=20 000` символов
- Score **47%**, совпало **22 из 34** с эталоном (+10 vs v3). CASE-007/010/011 подтверждены прогоном.
- Добавлен §15 в `CLAUDE_CODE_RULES.md` — защита эталонных файлов от перезаписи.
- Попутно исправлен баг `_resolve_output_path` (URL без схемы давал hostname=None).
- Коммиты: `1015c7f` (прогон v4), `b3bd145` (§15)

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (2.2 → ✅). DECISIONS, CASES, PATTERNS, GOLDEN_SET_MAPPING — не трогались. RULES — добавлен §15.

### 2026-04-14 — Сессия 2.3: синхронизация _extract_privacy_policy между краулерами

- 5 расхождений между `crawler.py` и `playwright_crawler.py` устранены.
- `_is_russian_text()` теперь используется в `PlaywrightCrawler` (защита от повторения CASE-010 на OCR-текстах с пробелами).
- Лимит обрезки текста: 20 000 → 100 000 символов.
- Поля провенанса `text_hash`, `fetched_at`, `content_length` добавлены.
- `_extract_privacy_policy_from_text` вынесен как отдельный метод; `_extract_privacy_policy` — тонкая обёртка; PDF-ветка в `_crawl` делегирует в него же.
- Тесты: 216 → **219 passed**, 0 failed. 3 новых теста.
- Коммиты: `58e93cc`, `b78942c` — в origin.

**Документы:** PASSPORT (обновлено), NEXT_SESSIONS_PLAN (сессия 2.3 добавлена как ✅ Выполнено). DECISIONS, CASES, PATTERNS, GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-14 — Сессия 2.4: ускорение тестов (js_render_delay)

- `js_render_delay` вынесен как параметр `PlaywrightCrawler.__init__` (default 2.0, в тестах 0).
- `asyncio.sleep(2)` в `_crawl` и `asyncio.sleep(1)` в `_try_fallback_privacy_urls` заменены на `self.js_render_delay` / `self.js_render_delay / 2`.
- Продакшн-поведение не изменилось (дефолт 2.0). Тесты: **219 passed, 56 с → 11 с (×5)**.
- Коммит: `17aaf7a` — в origin.

**Документы:** PASSPORT (обновлено). NEXT_SESSIONS_PLAN, DECISIONS, CASES, PATTERNS, GOLDEN_SET_MAPPING, RULES — не трогались.

### 2026-04-14 — Техническая правка: ускорение test_truncates_long_text

- `_clean_html_text` в `src/llm/web_tools.py`: добавлена одна строка — обрезка входного HTML до `MAX_PAGE_TEXT * 20` (240 000 символов) **до** парсинга BeautifulSoup.
- Тест строил DOM из 100 000 тегов (~700 KB), хотя вывод обрезался до 12 000 символов. Парсинг всего DOM занимал ~4–6 с.
- Результат: `test_truncates_long_text` ~6 с → **~1 с**. Тесты: **219 passed**.
- Коммит: `58172e5`.

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
https://github.com/Nataly369264/compliance152 (открытый)

Команда

Дмитрий — первоначальный прототип

Наталья — со-разработка, тестирование

Claude Code — разработка и исправление багов

Perplexity/Claude(claude.ai) — стратегия сессий, контроль контекста

Известные особенности
Windows-прокси: httpx.Client(trust_env=False) — обязательно

Папка проекта содержит кириллицу в имени — bash не работает, только PowerShell или терминал VS Code

SESSION_*.md исключены из Git — хранятся локально

