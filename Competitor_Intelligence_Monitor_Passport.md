Competitor_Intelligence_Monitor_Passport

Концепция модуля для проекта AI-сканер 152-ФЗ
Назначение
Модуль автономно отслеживает изменения у конкурентов и в законодательстве, формирует еженедельный дайджест с LLM-анализом — без ручного мониторинга.

Принцип работы: снапшот страницы → сравнение с предыдущим → LLM-анализ → дайджест → Telegram-уведомление.

Структура файлов:

competitor_monitor/
├── config/
│   └── sources.yaml          # все источники: конкуренты + НПА
├── modules/
│   ├── fetcher.py             # загрузка страниц + антибот + retry
│   ├── diff_engine.py         # сравнение снапшотов + pre-filter
│   ├── npa_watcher.py         # парсинг НПА + parse_warning
│   ├── analyzer_llm.py        # LLM-анализ с очередью + threat_score
│   ├── reporter.py            # генерация дайджеста
│   └── notifier.py            # Telegram-уведомления
├── scheduler.py               # APScheduler — расписание задач
└── main.py                    # точка входа / ручной запуск

sources.yaml — конфигурация источников

competitors:
  - id: quickaudit
    name: "QuickAudit"
    js_render: false
    llm_analyze: true
    urls:
      - https://quickaudit.ru
      - https://quickaudit.ru/pricing
    watch: [features, pricing]

  - id: saitscan
    name: "SaitScan"
    js_render: false
    llm_analyze: true
    urls:
      - https://saitscan.ru
      - https://saitscan.ru/price
    watch: [features, pricing]

  - id: regulaguard
    name: "RegulaGuard"
    js_render: false
    llm_analyze: true
    urls:
      - https://www.regulaguard.ru
    watch: [features]

  - id: 1ps_152
    name: "1PS.ru / 152-ФЗ"
    js_render: false
    llm_analyze: false        # менее приоритетный — без LLM
    urls:
      - https://1ps.ru/instrumentyi-i-kalkulyatoryi/proverka-sajta-na-sootvetstvie-152-fz/
    watch: [features]

npa_sources:
  - id: rkn_news
    name: "РКН — новости"
    url: https://rkn.gov.ru/news/
    type: html_list
    npa_critical: true

  - id: consultant_152fz
    name: "КонсультантПлюс — 152-ФЗ"
    url: https://www.consultant.ru/document/cons_doc_LAW_61801/
    type: html_hash
    npa_critical: true

  - id: kremlin_laws
    name: "Кремль — новые законы"
    url: http://kremlin.ru/acts/bank
    type: html_list
    npa_critical: false

scheduler:
  competitor_check: "0 9 * * 1"    # каждый понедельник 09:00
  npa_check:        "0 9 * * *"    # ежедневно 09:00
  digest_generate:  "0 10 * * 1"   # каждый понедельник 10:00

➕ Добавить нового конкурента — просто дописать блок в этот файл. Код трогать не нужно.

Схема БД — 3 новые таблицы в compliance.db

-- Снапшоты страниц конкурентов
CREATE TABLE competitor_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL,
    content_hash TEXT NOT NULL,       -- SHA-256 от очищенного текста
    raw_text     TEXT,
    fetch_status TEXT DEFAULT 'ok',   -- 'ok' | 'timeout' | 'blocked' | 'error'
    captured_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Зафиксированные изменения
CREATE TABLE competitor_changes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          TEXT NOT NULL,
    url                TEXT NOT NULL,
    diff_summary       TEXT,          -- LLM-резюме (если llm_analyze: true)
    change_type        TEXT,          -- 'feature'|'pricing'|'ui'|'npa'|'minor'
    threat_score       INTEGER,       -- 1–5, выставляет LLM
    npa_critical       BOOLEAN DEFAULT FALSE,
    detected_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    included_in_digest BOOLEAN DEFAULT FALSE
);

-- Дайджесты
CREATE TABLE digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start DATE,
    period_end   DATE,
    content_md   TEXT,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

Логика каждого модуля
fetcher.py
Загружает страницы через httpx с ротацией User-Agent из пула 10+ агентов

Случайная задержка random.uniform(3, 8) секунд между запросами — антибот-защита

Retry: 3 попытки с экспоненциальной задержкой; при неудаче записывает fetch_status = 'error' / 'blocked' / 'timeout'

Если в sources.yaml флаг js_render: true — использует playwright вместо httpx

BeautifulSoup вырезает <header>, <footer>, <aside>, <nav>, <script>, <style> — оставляет только смысловой контент: <main>, <article>, .content

Считает SHA-256 от очищенного текста, сравнивает с последним снапшотом в БД

Если hash изменился → возвращает (old_text, new_text) для анализа; иначе — пропускает

diff_engine.py
Строит unified diff через стандартный difflib

Pre-filter: проверяет наличие ключевых слов в diff: ['цена', 'тариф', 'функци', 'проверк', 'штраф', 'новый', 'добавил', 'обновл']

Если ни одного ключевого слова → change_type = 'minor', LLM не вызывается, изменение просто записывается в БД

Если ключевые слова найдены и llm_analyze: true → передаёт diff в analyzer_llm.py

analyzer_llm.py
Очередь с rate-limit guard: запросы к OpenRouter идут последовательно с паузой 5 секунд между ними — защита от исчерпания лимитов бесплатного тарифа

Промпт для анализа конкурентов:

Ты — аналитик продукта в области compliance 152-ФЗ.
Тебе дан diff изменений страницы конкурента [name].
Определи:
1. Что изменилось (новые функции, цены, формулировки)?
2. Уровень угрозы для нашего продукта по шкале 1–5?
3. Требует ли изменение наших действий? (да/нет + что именно)
Верни JSON:
{
  "summary": "...",
  "change_type": "feature|pricing|ui|minor",
  "threat_score": 1-5,
  "action_required": true/false,
  "action": "..."
}

Промпт для анализа НПА:

Ты — юрист в области персональных данных РФ.
Тебе дан diff изменений законодательного источника [name].
Определи:
1. Какие статьи изменились?
2. Затронуты ли 152-ФЗ или ст. 13.11 КоАП?
3. Что нужно обновить в продукте? (чеклист, шаблоны, калькулятор штрафов)
Верни JSON:
{
  "summary": "...",
  "articles_affected": [...],
  "npa_critical": true/false,
  "product_updates": [...]
}

npa_watcher.py
Для type: html_list — парсит список новостей/актов, сравнивает по датам с последним запуском

Для type: html_hash — отслеживает hash полного текста документа

parse_warning: после парсинга проверяет len(found_items) > 0; если 0 — логирует предупреждение и отправляет Telegram-алерт (структура сайта могла сломаться)

При npa_critical: true — немедленно вызывает notifier.py, не ждёт понедельничного дайджеста

reporter.py
Собирает все записи из competitor_changes за период, где included_in_digest = FALSE

Сортирует по threat_score DESC — самые важные изменения наверху

Генерирует Markdown-дайджест:

# 📊 Дайджест конкурентного мониторинга
## Период: [date] — [date]

### 🏁 Конкуренты
#### QuickAudit  ⚠️ threat: 4/5
- [feature] Добавили проверку META-тегов
  → Действие: рассмотреть включение в чеклист

### 📜 Изменения в законодательстве
- [КРИТИЧНО] Обновлена редакция ст. 13.11 КоАП
  → Обновить: calculator.py, шаблон №3

### 💡 Топ-3 приоритета на неделю
1. ...
2. ...
3. ...

Сохраняет дайджест в таблицу digests, экспортирует в .md файл

Помечает все обработанные записи included_in_digest = TRUE

notifier.py
Единый модуль для всех Telegram-уведомлений. Три метода:

send_digest(md_text) — отправляет дайджест каждый понедельник

send_critical_alert(summary) — мгновенный алерт при npa_critical = True

send_parse_warning(source_id) — алерт при сломанном парсере НПА

Использует requests.post к Telegram Bot API, токен берётся из .env.

scheduler.py — расписание:
# Три независимые задачи
scheduler.add_job(run_npa_check,        'cron', hour=9)               # ежедневно
scheduler.add_job(run_competitor_check, 'cron', day_of_week='mon', hour=9)
scheduler.add_job(run_digest,           'cron', day_of_week='mon', hour=10)

Интеграция с основным проектом
Модуль полностью изолирован — существующий код не трогается:

project_root/
├── crawler.py             ← без изменений
├── analyzer.py            ← без изменений
├── generator.py           ← без изменений
├── compliance.db          ← +3 новые таблицы
├── main_api.py            ← +3 новых эндпоинта
└── competitor_monitor/    ← новый модуль ✨

Три новых эндпоинта в main_api.py:

GET /monitor/digest          # последний дайджест в Markdown
GET /monitor/digest/history  # список всех дайджестов
GET /monitor/changes         # последние изменения с threat_score

Этапы реализации для Claude Code

| Этап | Задача                                                                  | Файлы                                     |
| ---- | ----------------------------------------------------------------------- | ----------------------------------------- |
| 1    | Создать таблицы БД + sources.yaml + fetcher.py с антиботом и retry      | sources.yaml, fetcher.py, миграция БД     |
| 2    | diff_engine.py с pre-filter + analyzer_llm.py с очередью и JSON-ответом | diff_engine.py, analyzer_llm.py           |
| 3    | npa_watcher.py с parse_warning + notifier.py                            | npa_watcher.py, notifier.py               |
| 4    | reporter.py с сортировкой по threat_score и Markdown-дайджестом         | reporter.py                               |
| 5    | scheduler.py + main.py + эндпоинты в FastAPI                            | scheduler.py, main.py, правки main_api.py |

