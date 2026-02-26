# compliance152 — Подробная архитектура агента

## Содержание

1. [Общая схема работы](#1-общая-схема-работы)
2. [Поток данных (Data Flow)](#2-поток-данных)
3. [Модуль Scanner — детальная архитектура](#3-scanner)
4. [Модуль Analyzer — детальная архитектура](#4-analyzer)
5. [Модуль Generator — детальная архитектура](#5-generator)
6. [Модуль Monitor — детальная архитектура](#6-monitor)
7. [Модуль Updater — детальная архитектура](#7-updater)
8. [LLM-клиент — архитектура взаимодействия с Claude](#8-llm)
9. [База знаний](#9-knowledge-base)
10. [Хранилище (Storage)](#10-storage)
11. [API — структура эндпоинтов](#11-api)
12. [Модели данных](#12-models)
13. [Обработка ошибок и устойчивость](#13-errors)
14. [Конфигурация и окружение](#14-config)

---

## 1. Общая схема работы

```
┌─────────────────────────────────────────────────────────────────┐
│                        КЛИЕНТ (API)                             │
│  POST /scan  POST /analyze  POST /generate  GET /reports  ...   │
└───────┬──────────┬──────────────┬───────────────┬───────────────┘
        │          │              │               │
        ▼          ▼              ▼               ▼
┌──────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────┐
│ Scanner  │→│ Analyzer │ │ Generator │ │   Monitor   │
│          │ │          │ │           │ │  (planned)  │
│ crawl    │ │ checklist│ │ templates │ │             │
│ detect   │ │ LLM      │ │ LLM       │ │  crawl law  │
│ extract  │ │ scoring  │ │ validate  │ │  LLM parse  │
└────┬─────┘ └────┬─────┘ └─────┬─────┘ └──────┬──────┘
     │            │             │               │
     │            │             │               ▼
     │            │             │        ┌───────────┐
     │            │             │        │  Updater  │
     │            │             │        │ (planned) │
     │            │             │        │           │
     │            │             │        │ diff docs │
     │            │             │        │ new vers  │
     │            │             │        └─────┬─────┘
     │            │             │              │
     ▼            ▼             ▼              ▼
┌─────────────────────────────────────────────────────┐
│                    LLM Client                       │
│         (Claude API через Anthropic SDK)            │
│  async │ retry │ backoff │ rate limiting            │
└─────────────────────────────────────────────────────┘
     │            │             │              │
     ▼            ▼             ▼              ▼
┌─────────────────────────────────────────────────────┐
│                    Storage (SQLite)                  │
│  organizations │ scans │ reports │ documents │ vers  │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│                  Knowledge Base                     │
│  checklists │ fine_schedule │ prohibited_services   │
│  templates  │ legal_updates                         │
└─────────────────────────────────────────────────────┘
```

---

## 2. Поток данных

### Сценарий 1: Полный аудит сайта

```
Клиент                  API                Scanner           Analyzer          LLM            DB
  │                      │                    │                 │                │              │
  │ POST /api/v1/analyze │                    │                 │                │              │
  │ {url: "example.com"} │                    │                 │                │              │
  │─────────────────────>│                    │                 │                │              │
  │                      │ scan(url)          │                 │                │              │
  │                      │───────────────────>│                 │                │              │
  │                      │                    │ BFS краулинг    │                │              │
  │                      │                    │ (до 100 стр.)  │                │              │
  │                      │                    │ ───────┐        │                │              │
  │                      │                    │        │ extract│                │              │
  │                      │                    │        │ forms  │                │              │
  │                      │                    │        │ cookies│                │              │
  │                      │                    │        │ scripts│                │              │
  │                      │                    │        │ policy │                │              │
  │                      │                    │ <──────┘        │                │              │
  │                      │   ScanResult       │                 │                │              │
  │                      │<───────────────────│                 │                │              │
  │                      │                    │                 │                │              │
  │                      │ save_scan(result)  │                 │                │           │
  │                      │─────────────────────────────────────────────────────────────────>│
  │                      │                    │                 │                │              │
  │                      │ analyze(result)    │                 │                │              │
  │                      │─────────────────────────────────────>│                │              │
  │                      │                    │                 │                │              │
  │                      │                    │                 │ _check_forms() │              │
  │                      │                    │                 │ _check_cookies()              │
  │                      │                    │                 │ _check_policy()│              │
  │                      │                    │                 │ _check_tech()  │              │
  │                      │                    │                 │ _check_reg()   │              │
  │                      │                    │                 │                │              │
  │                      │                    │                 │ policy → LLM   │              │
  │                      │                    │                 │───────────────>│              │
  │                      │                    │                 │  JSON-анализ   │              │
  │                      │                    │                 │<───────────────│              │
  │                      │                    │                 │                │              │
  │                      │                    │                 │ summary → LLM  │              │
  │                      │                    │                 │───────────────>│              │
  │                      │                    │                 │  текст отчёта  │              │
  │                      │                    │                 │<───────────────│              │
  │                      │                    │                 │                │              │
  │                      │  ComplianceReport  │                 │                │              │
  │                      │<─────────────────────────────────────│                │              │
  │                      │                    │                 │                │              │
  │                      │ save_report()      │                 │                │              │
  │                      │─────────────────────────────────────────────────────────────────>│
  │                      │                    │                 │                │              │
  │ AnalyzeResponse      │                    │                 │                │              │
  │ {score, risk,        │                    │                 │                │              │
  │  violations, fines,  │                    │                 │                │              │
  │  summary}            │                    │                 │                │              │
  │<─────────────────────│                    │                 │                │              │
```

### Сценарий 2: Генерация документов

```
Клиент                API              Generator         Knowledge       LLM           DB
  │                    │                   │                 │              │             │
  │ POST /generate     │                   │                 │              │             │
  │ {org_id, types}    │                   │                 │              │             │
  │───────────────────>│                   │                 │              │             │
  │                    │ get_org(org_id)    │                 │              │             │
  │                    │──────────────────────────────────────────────────────────────>│
  │                    │ org_data           │                 │              │             │
  │                    │<─────────────────────────────────────────────────────────────│
  │                    │                   │                 │              │             │
  │                    │ for each doc_type:│                 │              │             │
  │                    │──────────────────>│                 │              │             │
  │                    │                   │ load_template() │              │             │
  │                    │                   │────────────────>│              │             │
  │                    │                   │ template.md     │              │             │
  │                    │                   │<────────────────│              │             │
  │                    │                   │                 │              │             │
  │                    │                   │ get_updates()   │              │             │
  │                    │                   │────────────────>│              │             │
  │                    │                   │ legal_context   │              │             │
  │                    │                   │<────────────────│              │             │
  │                    │                   │                 │              │             │
  │                    │                   │ prompt = org_data + template + legal_ctx   │
  │                    │                   │─────────────────────────────>│             │
  │                    │                   │         generated_document   │             │
  │                    │                   │<─────────────────────────────│             │
  │                    │                   │                 │              │             │
  │                    │ save_document()   │                 │              │             │
  │                    │──────────────────────────────────────────────────────────────>│
  │                    │                   │                 │              │             │
  │ {documents, errors}│                   │                 │              │             │
  │<───────────────────│                   │                 │              │             │
```

### Сценарий 3: Мониторинг + автообновление (планируемый)

```
Cron/Scheduler       Monitor           LLM          Updater          Generator        DB
  │                    │                 │              │                 │              │
  │ trigger            │                 │              │                 │              │
  │───────────────────>│                 │              │                 │              │
  │                    │ crawl sources:  │              │                 │              │
  │                    │ - rkn.gov.ru    │              │                 │              │
  │                    │ - pravo.gov.ru  │              │                 │              │
  │                    │ - consultant    │              │                 │              │
  │                    │                 │              │                 │              │
  │                    │ new_text → LLM  │              │                 │              │
  │                    │────────────────>│              │                 │              │
  │                    │ LegalUpdate     │              │                 │              │
  │                    │<────────────────│              │                 │              │
  │                    │                 │              │                 │              │
  │                    │ save_update()   │              │                 │              │
  │                    │─────────────────────────────────────────────────────────────>│
  │                    │                 │              │                 │              │
  │                    │ notify_updater  │              │                 │              │
  │                    │────────────────────────────>│                 │              │
  │                    │                 │              │                 │              │
  │                    │                 │              │ find affected   │              │
  │                    │                 │              │ orgs + docs     │              │
  │                    │                 │              │──────────────────────────────>│
  │                    │                 │              │ docs_to_update  │              │
  │                    │                 │              │<─────────────────────────────│
  │                    │                 │              │                 │              │
  │                    │                 │              │ for each doc:   │              │
  │                    │                 │              │ regenerate()    │              │
  │                    │                 │              │────────────────>│              │
  │                    │                 │              │ new_version     │              │
  │                    │                 │              │<────────────────│              │
  │                    │                 │              │                 │              │
  │                    │                 │              │ save_version()  │              │
  │                    │                 │              │──────────────────────────────>│
  │                    │                 │              │                 │              │
  │                    │                 │              │ notify_client   │              │
  │                    │                 │              │ (email/webhook) │              │
```

---

## 3. Scanner — детальная архитектура

### Класс `SiteScanner`

```
SiteScanner(url, max_pages=100, timeout=30, delay=1.0)
│
├── scan(url) → ScanResult                    # Главный метод
│   │
│   ├── 1. Инициализация httpx.AsyncClient
│   │      User-Agent: "Compliance152Bot/1.0"
│   │      follow_redirects: True
│   │      timeout: 30s
│   │
│   ├── 2. BFS-краулинг
│   │   ├── Очередь URL (deque) с приоритетом для privacy policy
│   │   ├── Множество visited для дедупликации
│   │   ├── Ограничение: max_pages (по умолчанию 100)
│   │   ├── Задержка между запросами: crawl_delay (1 сек)
│   │   ├── Фильтрация: пропуск бинарных файлов (.jpg, .css, .js, .zip, .woff...)
│   │   └── Только тот же домен (same-domain check)
│   │
│   ├── 3. Для каждой страницы:
│   │   ├── GET запрос → HTML
│   │   ├── BeautifulSoup парсинг
│   │   ├── _extract_forms(soup, page_url)
│   │   │   ├── Найти все <form>
│   │   │   ├── Для каждой формы:
│   │   │   │   ├── Извлечь поля (input, select, textarea)
│   │   │   │   ├── detect_personal_data_fields() → категории ПДн
│   │   │   │   ├── detect_consent_checkbox() → наличие, предвыбор, текст
│   │   │   │   └── detect_privacy_link() → ссылка на политику
│   │   │   └── Вернуть list[FormInfo]
│   │   │
│   │   ├── detect_external_scripts(soup, url)
│   │   │   ├── <script src="..."> с внешних доменов
│   │   │   ├── <link rel="stylesheet" href="..."> с внешних доменов
│   │   │   ├── <link rel="preconnect"> (Google Fonts)
│   │   │   ├── <iframe src="...">
│   │   │   ├── <img> 1x1 (tracking pixels)
│   │   │   └── Для каждого: get_prohibited_service_by_domain()
│   │   │
│   │   ├── detect_cookie_banner(soup)
│   │   │   ├── Поиск по CSS-классам/ID: cookie-banner, cookie-consent, cookie-notice...
│   │   │   ├── Поиск cookie-consent скриптов (CookieBot, OneTrust...)
│   │   │   ├── Кнопки: accept, decline, categories
│   │   │   └── Вернуть CookieBannerInfo
│   │   │
│   │   ├── detect_footer_privacy_link(soup)
│   │   │   ├── Ищет <footer> или последние 25% ссылок
│   │   │   └── Ищет текст/href с "политик", "privacy", "персональн"
│   │   │
│   │   ├── is_privacy_policy_page(url, title)
│   │   │   └── Эвристика по URL и <title>
│   │   │
│   │   └── Если это страница политики:
│   │       └── _extract_privacy_policy(soup, url)
│   │           ├── Извлечение полного текста (до 15000 символов)
│   │           └── 16 regex-проверок содержания:
│   │               ├── has_operator_name  (наименование оператора)
│   │               ├── has_inn_ogrn       (ИНН/ОГРН)
│   │               ├── has_responsible    (ответственный)
│   │               ├── has_data_categories (категории данных)
│   │               ├── has_purposes       (цели обработки)
│   │               ├── has_legal_basis    (правовые основания)
│   │               ├── has_retention      (сроки хранения)
│   │               ├── has_subject_rights (права субъектов)
│   │               ├── has_rights_procedure (порядок реализации прав)
│   │               ├── has_cross_border   (трансграничная передача)
│   │               ├── has_security       (меры безопасности)
│   │               ├── has_cookies        (информация о cookie)
│   │               ├── has_localization   (локализация данных)
│   │               ├── has_date           (дата публикации)
│   │               ├── is_russian         (документ на русском)
│   │               └── is_separate_page   (отдельная страница)
│   │
│   ├── 4. Проверка SSL
│   │   └── _check_ssl(url) → SSLInfo
│   │       └── HEAD https://... → valid/invalid/absent
│   │
│   └── 5. Сборка ScanResult
│       ├── url, pages[], forms[], cookies[]
│       ├── external_scripts[], privacy_policy
│       ├── ssl_status, cookie_banner
│       └── Подсчёт статистики
```

### Детекторы (`detectors.py`)

```
Детекторы — чистые функции, работающие с BeautifulSoup-объектами.
Не делают HTTP-запросов, не зависят от состояния.

detect_personal_data_fields(fields: list[FormField]) → dict[str, list[str]]
  Паттерны (regex, регистронезависимые):
  ├── name:     "имя|фамил|отчеств|фио|name|first.?name|last.?name|surname"
  ├── email:    "email|e-mail|почт|электрон"
  ├── phone:    "телеф|phone|mobile|моби|сотов"
  ├── address:  "адрес|address|город|city|индекс|postal"
  ├── passport: "паспорт|passport|серия|номер.?документ"
  ├── inn:      "инн|inn|taxpayer"
  ├── snils:    "снилс|snils|пенсион"
  └── birthday: "дата.?рожд|birthday|birth.?date|возраст|age"

detect_consent_checkbox(form_soup) → (has_consent, is_prechecked, text)
  Ищет: <input type="checkbox"> + текст "соглас|согласи|consent|обработк|персональн"

detect_privacy_link(form_soup, page_soup) → (has_link, url, text)
  Ищет: <a href="..."> с текстом "политик|privacy|конфиденциальн|персональн"

detect_cookie_banner(soup) → CookieBannerInfo
  CSS-паттерны: "cookie-banner", "cookie-consent", "cookie-notice",
                "cc-banner", "gdpr", "cookie-popup"
  Скрипт-паттерны: "cookiebot", "onetrust", "cookie-consent"
```

---

## 4. Analyzer — детальная архитектура

### Класс `ComplianceAnalyzer`

```
ComplianceAnalyzer(scan_result: ScanResult, llm_client: LLMClient)
│
├── analyze() → ComplianceReport
│   │
│   ├── 1. Проверки по чеклисту (синхронные, на основе ScanResult)
│   │
│   │   ├── _check_forms()  ─── Для каждой формы в scan_result.forms:
│   │   │   ├── FORM_001: Есть ли чекбокс согласия?
│   │   │   │   └── fail → severity: critical, штраф: 300к-700к
│   │   │   ├── FORM_002: Чекбокс не предвыбран?
│   │   │   │   └── fail → severity: high
│   │   │   ├── FORM_003: Есть ли ссылка на политику рядом?
│   │   │   │   └── fail → severity: high
│   │   │   ├── FORM_006: Маркетинговый чекбокс отдельно?
│   │   │   │   └── warning (если есть рассылочные поля)
│   │   │   └── FORM_007: Избыточные поля?
│   │   │       └── warning (если полей > 7)
│   │   │
│   │   ├── _check_cookies()
│   │   │   ├── COOKIE_001: Cookie-баннер присутствует?
│   │   │   │   └── fail → severity: high, штраф: 150к-300к
│   │   │   ├── COOKIE_002: Кнопка "Отклонить" есть?
│   │   │   │   └── fail → severity: high
│   │   │   ├── COOKIE_003: Выбор категорий cookie?
│   │   │   │   └── fail → severity: medium
│   │   │   └── COOKIE_005: Аналитика до согласия?
│   │   │       └── fail → severity: critical, штраф: 300к-700к
│   │   │
│   │   ├── _check_privacy_policy()
│   │   │   ├── POLICY_001: Политика опубликована?
│   │   │   │   └── fail → severity: critical
│   │   │   ├── POLICY_002: Ссылка в футере каждой страницы?
│   │   │   │   └── fail → severity: high
│   │   │   ├── POLICY_003–016: 14 проверок содержания политики
│   │   │   │   (наименование, ИНН, ответственный, категории,
│   │   │   │    цели, основания, сроки, права, порядок,
│   │   │   │    трансграничная, безопасность, cookie,
│   │   │   │    локализация, дата)
│   │   │   │   └── fail/pass на основе regex-флагов из ScanResult
│   │   │   └── POLICY_017: Политика на отдельной странице?
│   │   │
│   │   ├── _check_technical()
│   │   │   ├── TECH_001: HTTPS на всех страницах?
│   │   │   │   └── fail → severity: high
│   │   │   ├── TECH_002: Google Fonts? → severity: medium
│   │   │   ├── TECH_003: Google Analytics? → severity: critical, штраф: 1М-6М
│   │   │   ├── TECH_004: Facebook Pixel? → severity: critical
│   │   │   ├── TECH_005: Google reCAPTCHA? → severity: high
│   │   │   └── TECH_006: Google Tag Manager? → severity: high
│   │   │
│   │   └── _check_regulatory()
│   │       ├── REG_001: Реестр РКН → manual_check
│   │       ├── REG_002: Уведомление в РКН → manual_check
│   │       └── REG_003: Хостинг в РФ → manual_check
│   │
│   ├── 2. LLM-анализ политики (async)
│   │   └── _analyze_policy_with_llm()
│   │       ├── Вход: текст политики (до 15000 символов)
│   │       ├── System prompt: эксперт по 152-ФЗ
│   │       ├── Выход: JSON с 16 критериями
│   │       │   Для каждого критерия:
│   │       │   ├── status: "соответствует" | "не соответствует" | "частично"
│   │       │   ├── quote: цитата из документа
│   │       │   ├── law_reference: ссылка на статью закона
│   │       │   └── recommendation: рекомендация по исправлению
│   │       └── При ошибке: llm_analysis = None (отчёт всё равно формируется)
│   │
│   ├── 3. Генерация резюме (async)
│   │   └── _generate_summary()
│   │       ├── System prompt: знает об изменениях 2025 года
│   │       ├── Вход: статистика скана + список нарушений
│   │       ├── Выход: текст из 3 частей:
│   │       │   ├── Общая оценка состояния
│   │       │   ├── ТОП-5 критичных проблем
│   │       │   └── Оценка рисков и штрафов
│   │       └── Fallback: _fallback_summary() при ошибке LLM
│   │
│   ├── 4. Расчёт скоринга
│   │   ├── overall_score: 0-100
│   │   │   100 - (critical * 15) - (high * 8) - (medium * 3) - (low * 1)
│   │   │   (не ниже 0)
│   │   │
│   │   └── _calculate_risk_level():
│   │       ├── "critical" — если critical >= 3 ИЛИ score < 30
│   │       ├── "high"     — если critical >= 1 ИЛИ score < 50
│   │       ├── "medium"   — если score < 75
│   │       └── "low"      — иначе
│   │
│   └── 5. Оценка штрафов
│       └── estimate_fines(violation_ids) → FineEstimate
│           ├── Маппинг: FORM_ → "no_consent" (300к-700к)
│           ├── Маппинг: COOKIE_ → "no_cookie_banner" (150к-300к)
│           ├── Маппинг: TECH_ → "prohibited_services" (1М-6М)
│           └── Суммирование min/max по всем нарушениям
```

### Схема скоринга

```
Нарушение        │ Severity │ Штраф (первое)  │ Штраф (повтор)  │ Баллы (-)
─────────────────┼──────────┼─────────────────┼─────────────────┼──────────
Нет согласия     │ critical │ 300к - 700к     │ до 1.5М         │ -15
Google Analytics │ critical │ 1М - 6М         │ до 18М          │ -15
Facebook Pixel   │ critical │ 1М - 6М         │ до 18М          │ -15
Аналит. до согл. │ critical │ 300к - 700к     │ до 1.5М         │ -15
Нет политики     │ critical │ 300к - 700к     │ до 1.5М         │ -15
Нет баннера      │ high     │ 150к - 300к     │ до 500к         │ -8
Предвыбр. чекбокс│ high     │ 100к - 300к     │ -               │ -8
Нет HTTPS        │ high     │ -               │ -               │ -8
Нет категорий    │ medium   │ -               │ -               │ -3
Нет даты         │ low      │ -               │ -               │ -1
```

---

## 5. Generator — детальная архитектура

### Класс `DocumentGenerator`

```
DocumentGenerator(organization: OrganizationData, llm_client: LLMClient)
│
├── 12 типов документов (DOCUMENT_TYPES):
│   │
│   │ С шаблонами (8):                    Без шаблонов (4):
│   ├── privacy_policy                    ├── rkn_notification
│   ├── consent_form                      ├── consent_withdrawal_form
│   ├── cookie_policy                     ├── data_processing_agreement
│   ├── responsible_person_order          └── employee_consent
│   ├── processing_regulation
│   ├── harm_assessment
│   ├── nondisclosure_agreement
│   └── incident_instruction
│
├── generate_document(doc_type) → dict
│   │
│   ├── 1. Валидация типа документа
│   │
│   ├── 2. Загрузка шаблона
│   │   ├── Если template_file != None:
│   │   │   └── Читает knowledge_base/templates/{template_file}.md
│   │   └── Если template_file == None:
│   │       └── Возвращает "Шаблон отсутствует. Сгенерируйте полностью."
│   │
│   ├── 3. Загрузка правового контекста
│   │   ├── get_updates_for_document_active(doc_type, today)
│   │   │   └── Фильтрует legal_updates по:
│   │   │       ├── doc_type ∈ affected_documents
│   │   │       └── effective_date <= today
│   │   └── format_legal_context(updates)
│   │       └── Форматирует в текстовый блок:
│   │           "== АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ==
│   │            [дата] [источник]: [название]
│   │            Требования: ...
│   │            Затронутые документы: ..."
│   │
│   ├── 4. Формирование промпта
│   │   ├── System: "Вы — эксперт-юрист по персональным данным..."
│   │   │   └── Требования: строгий юридический стиль, ссылки на статьи,
│   │   │       Markdown, учёт правового контекста
│   │   └── User: заполненный шаблон с:
│   │       ├── Все 20+ полей OrganizationData
│   │       ├── {legal_context} — актуальные изменения
│   │       └── {template_content} — текст шаблона
│   │
│   ├── 5. Вызов LLM
│   │   └── call(system, user, max_tokens=8192, temperature=0.2)
│   │
│   └── 6. Результат
│       └── {id, organization_id, doc_type, title, content_md, version, created_at}
│
├── generate_public_documents() → {documents: [...], errors: [...]}
│   └── Генерирует 3 документа: privacy_policy, consent_form, cookie_policy
│
└── generate_full_package() → {documents: [...], errors: [...]}
    └── Генерирует все 12 документов (продолжает при ошибках)
```

### Как шаблон превращается в документ

```
Шаблон (privacy_policy.md):                 Данные организации:
┌──────────────────────────────┐            ┌──────────────────────────┐
│ # Политика обработки ПДн     │            │ legal_name: ООО "Ромашка"│
│                              │            │ inn: 7712345678          │
│ 1. {{LEGAL_NAME}} является   │            │ ceo_name: Иванов И.И.   │
│    оператором ПДн...         │            │ data_categories:        │
│                              │     +      │   - ФИО                 │
│ 2. Категории: {{DATA_CAT}}  │            │   - email               │
│                              │            │   - телефон              │
│ 3. Цели: {{PURPOSES}}       │            │ purposes:               │
│    ...                       │            │   - оказание услуг      │
└──────────────────────────────┘            └──────────────────────────┘
         │                                            │
         └────────────────────┬───────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Правовой контекст: │
                    │ с 01.09.2025:      │
                    │ согласие — отдельный│
                    │ документ (420-ФЗ)  │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │    Claude API      │
                    │ (temperature=0.2)  │
                    │                    │
                    │ Подставляет данные  │
                    │ Адаптирует под     │
                    │ бизнес клиента     │
                    │ Учитывает новые    │
                    │ требования закона  │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────────────────┐
                    │ Готовый документ (Markdown):    │
                    │                                │
                    │ # Политика обработки ПДн       │
                    │ ООО "Ромашка" (ИНН 7712345678) │
                    │                                │
                    │ 1. Общество с ограниченной     │
                    │    ответственностью "Ромашка"   │
                    │    является оператором...       │
                    │                                │
                    │ 2. Категории ПДн:              │
                    │    - фамилия, имя, отчество    │
                    │    - адрес электронной почты    │
                    │    - номер телефона             │
                    │ ...                            │
                    └────────────────────────────────┘
```

---

## 6. Monitor — детальная архитектура (планируемый)

```
Monitor
│
├── Источники (краулеры):
│   │
│   ├── RKNMonitor
│   │   ├── URL: rkn.gov.ru/news, pd.rkn.gov.ru
│   │   ├── Частота: каждые 6 часов
│   │   ├── Метод: httpx + BeautifulSoup
│   │   └── Ищет: новости, приказы, разъяснения по тегу "персональные данные"
│   │
│   ├── LegalActsMonitor
│   │   ├── URL: publication.pravo.gov.ru
│   │   ├── Частота: раз в сутки
│   │   ├── Метод: httpx + BS4
│   │   └── Ищет: федеральные законы и постановления с ключевыми словами
│   │       "152-ФЗ", "персональные данные", "оператор"
│   │
│   ├── ConsultantMonitor
│   │   ├── URL: consultant.ru (поиск по 152-ФЗ)
│   │   ├── Частота: раз в сутки
│   │   └── Ищет: новые редакции закона, комментарии
│   │
│   └── CourtMonitor
│       ├── URL: kad.arbitr.ru, sudact.ru
│       ├── Частота: раз в неделю
│       └── Ищет: судебные решения по ст. 13.11 КоАП (штрафы за ПДн)
│
├── Обработка найденного:
│   │
│   ├── 1. Дедупликация (по URL + хэш содержимого)
│   ├── 2. LLM-анализ каждого нового документа:
│   │   ├── System: "Вы — эксперт по законодательству о ПДн..."
│   │   ├── Вход: текст нового акта/разъяснения
│   │   └── Выход (JSON):
│   │       ├── title: краткое название
│   │       ├── summary: что изменилось (3-5 предложений)
│   │       ├── articles: затронутые статьи 152-ФЗ
│   │       ├── affected_documents: какие из 12 типов документов затронуты
│   │       ├── requirements: конкретные новые требования
│   │       ├── severity: critical | important | informational
│   │       └── effective_date: дата вступления в силу
│   │
│   └── 3. Сохранение в БД (таблица legal_updates)
│
└── Выход: список LegalUpdate → передаётся в Updater
```

---

## 7. Updater — детальная архитектура (планируемый)

```
Updater
│
├── Вход: LegalUpdate от Monitor
│
├── Алгоритм:
│   │
│   ├── 1. Определить затронутые документы
│   │   ├── affected_documents из LegalUpdate
│   │   └── SQL: SELECT * FROM documents
│   │       WHERE doc_type IN (...affected_documents)
│   │
│   ├── 2. Для каждого затронутого документа каждой организации:
│   │   │
│   │   ├── a. Загрузить текущую версию документа из БД
│   │   │
│   │   ├── b. Сформировать промпт для обновления:
│   │   │   ├── System: "Вы — юрист. Обновите документ с учётом изменений."
│   │   │   ├── current_document: текущий текст документа
│   │   │   ├── legal_update: описание изменения
│   │   │   └── requirements: конкретные новые требования
│   │   │
│   │   ├── c. Claude генерирует обновлённую версию
│   │   │
│   │   ├── d. Diff между старой и новой версией
│   │   │   └── Используем difflib для наглядного сравнения
│   │   │
│   │   ├── e. Сохранить новую версию
│   │   │   ├── INSERT INTO document_versions (старая версия)
│   │   │   └── UPDATE documents SET content_md = new_content, version += 1
│   │   │
│   │   └── f. Уведомление клиенту
│   │       ├── Режим "авто": обновлено, вот diff
│   │       └── Режим "подтверждение": вот черновик, подтвердите
│   │
│   └── 3. Логирование всех действий
│
└── Выход:
    ├── Обновлённые документы в БД
    └── Уведомления (notifications) в БД
```

---

## 8. LLM-клиент — архитектура

### Класс `LLMClient`

```
LLMClient
│
├── __init__()
│   └── self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
│
├── call(system_prompt, user_prompt, max_tokens=8192, temperature=0.3) → str
│   │
│   ├── Retry-стратегия:
│   │   ├── Задержки: [1, 2, 4, 8, 16, 32, 60] секунд
│   │   ├── Макс. ожидание: 300 секунд суммарно
│   │   ├── Повторяемые ошибки:
│   │   │   ├── RateLimitError (429)
│   │   │   ├── 5xx серверные ошибки
│   │   │   └── Ошибки подключения (ConnectionError, TimeoutError)
│   │   └── Неповторяемые → сразу raise LLMError
│   │
│   ├── API-вызов:
│   │   └── client.messages.create(
│   │       model=CLAUDE_MODEL,           # claude-sonnet-4-5-20250929
│   │       max_tokens=max_tokens,        # 8192 по умолчанию
│   │       temperature=temperature,      # 0.3 для анализа, 0.2 для генерации
│   │       system=system_prompt,
│   │       messages=[{role: "user", content: user_prompt}]
│   │   )
│   │
│   └── Возврат: response.content[0].text
│
└── Синглтон: get_client() / call_llm()
```

### Промпты по модулям

```
Модуль     │ System Prompt                         │ Temperature │ max_tokens
───────────┼───────────────────────────────────────┼─────────────┼───────────
Analyzer   │ Эксперт по 152-ФЗ, JSON-формат,      │ 0.3         │ 8192
(policy)   │ 16 критериев проверки политики        │             │
───────────┼───────────────────────────────────────┼─────────────┼───────────
Analyzer   │ Аналитик, знает изменения 2025,       │ 0.3         │ 8192
(summary)  │ 3-частное резюме                      │             │
───────────┼───────────────────────────────────────┼─────────────┼───────────
Generator  │ Юрист-документовед, строгий стиль,    │ 0.2         │ 8192
           │ ссылки на статьи, Markdown,           │             │
           │ учёт правового контекста              │             │
───────────┼───────────────────────────────────────┼─────────────┼───────────
Monitor    │ Эксперт по законодательству ПДн,      │ 0.3         │ 4096
(planned)  │ JSON: summary, impact, severity       │             │
───────────┼───────────────────────────────────────┼─────────────┼───────────
Updater    │ Юрист, обновление документов с        │ 0.2         │ 8192
(planned)  │ учётом конкретного изменения закона   │             │
```

---

## 9. База знаний

```
knowledge_base/
│
├── checklists/
│   │
│   ├── website_checklist.json          # 40 пунктов проверки
│   │   ├── FORM_001–008   (8 проверок форм)
│   │   ├── COOKIE_001–006 (6 проверок cookie)
│   │   ├── POLICY_001–017 (17 проверок политики)
│   │   ├── TECH_001–006   (6 технических проверок)
│   │   └── REG_001–003    (3 регуляторных проверки)
│   │
│   ├── prohibited_services.json        # 12 запрещённых сервисов
│   │   ├── Google: Analytics, GTM, Fonts, reCAPTCHA, Maps
│   │   ├── Facebook Pixel
│   │   ├── Cloudflare Analytics
│   │   └── HubSpot, Intercom, Hotjar, Mixpanel, Segment
│   │   Каждый: name, domains[], type, alternative, description
│   │
│   └── fine_schedule.json              # 9 категорий штрафов
│       ├── Нет уведомления в РКН:    100к-300к
│       ├── Обработка без согласия:    300к-700к (повтор 1.5М)
│       ├── Утечка данных:             3М-15М (повтор 0.1-3% выручки, макс 500М)
│       ├── Утечка биометрии:          15М-20М
│       ├── Неуведомление об утечке:   1М-3М
│       ├── Запрещённые сервисы:       1М-6М (повтор 18М)
│       ├── Нет баннера cookie:        150к-300к
│       ├── Чувствит. cookie без согл.: 300к-700к
│       └── Нет регистрации в РКН:     100к-300к
│
├── legal_updates/
│   └── updates.json                    # 8 актуальных изменений (2025)
│       ├── LU-2025-001: Запрет иностранных сервисов (01.07.2025)
│       ├── LU-2025-002: Согласие — отдельный документ (01.09.2025)
│       ├── LU-2025-003: Обязательная оценка вреда (01.09.2025)
│       ├── LU-2025-004: Уведомление об утечке за 24ч (01.09.2025)
│       ├── LU-2025-005: Оборотные штрафы за утечки (30.05.2025)
│       ├── LU-2025-006: Ужесточение локализации (01.09.2025)
│       ├── LU-2025-007: Новая форма уведомления РКН (01.09.2025)
│       └── LU-2025-008: Обновлённые требования ИСПДн (01.03.2025)
│
└── templates/                          # 8 Markdown-шаблонов
    ├── privacy_policy.md               # 16 разделов, ~3000 слов
    ├── consent_form.md                 # Автономная форма согласия
    ├── cookie_policy.md                # 6 разделов о cookie
    ├── responsible_person_order.md     # Приказ о назначении ответственного
    ├── processing_regulation.md        # Положение об обработке (14 разделов)
    ├── harm_assessment.md              # Акт оценки вреда
    ├── nondisclosure_agreement.md      # Обязательство о неразглашении
    └── incident_instruction.md         # Инструкция по инцидентам (8 разделов)

    Все шаблоны используют {{PLACEHOLDER}} для подстановки через LLM.
```

---

## 10. Хранилище (Storage)

### Схема базы данных

```sql
┌─────────────────────────────────────────────┐
│                organizations                 │
├─────────────────────────────────────────────┤
│ id              TEXT PK                      │
│ legal_name      TEXT NOT NULL                │
│ short_name      TEXT                         │
│ inn             TEXT NOT NULL                │
│ ogrn            TEXT                         │
│ legal_address   TEXT                         │
│ actual_address  TEXT                         │
│ ceo_name        TEXT                         │
│ ceo_position    TEXT                         │
│ responsible_person    TEXT                   │
│ responsible_contact   TEXT                   │
│ website_url     TEXT                         │
│ email           TEXT                         │
│ phone           TEXT                         │
│ data_categories       TEXT (JSON array)      │
│ processing_purposes   TEXT (JSON array)      │
│ data_subjects         TEXT (JSON array)      │
│ third_parties         TEXT (JSON array)      │
│ cross_border          INTEGER (bool)         │
│ cross_border_countries TEXT (JSON array)     │
│ hosting_location      TEXT                   │
│ info_systems          TEXT (JSON array)      │
│ created_at      TEXT                         │
│ updated_at      TEXT                         │
└──────────┬──────────────────────────────────┘
           │ 1
           │
           │ N
┌──────────▼──────────────────────────────────┐
│                   scans                      │
├─────────────────────────────────────────────┤
│ id              TEXT PK                      │
│ organization_id TEXT FK → organizations      │
│ site_url        TEXT NOT NULL                │
│ scan_date       TEXT                         │
│ result_json     TEXT (полный ScanResult)     │
│ pages_scanned   INTEGER                      │
└──────────┬──────────────────────────────────┘
           │ 1
           │
           │ N
┌──────────▼──────────────────────────────────┐
│            compliance_reports                 │
├─────────────────────────────────────────────┤
│ id              TEXT PK                      │
│ scan_id         TEXT FK → scans              │
│ organization_id TEXT FK → organizations      │
│ report_date     TEXT                         │
│ overall_score   INTEGER                      │
│ risk_level      TEXT                         │
│ violations_count INTEGER                     │
│ report_json     TEXT (полный ComplianceReport)│
└─────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│                  documents                    │
├──────────────────────────────────────────────┤
│ id              TEXT PK                       │
│ organization_id TEXT FK → organizations       │
│ doc_type        TEXT NOT NULL                 │
│ title           TEXT                          │
│ version         INTEGER DEFAULT 1             │
│ content_md      TEXT (Markdown)               │
│ content_html    TEXT (HTML, nullable)         │
│ created_at      TEXT                          │
│ updated_at      TEXT                          │
└──────────┬───────────────────────────────────┘
           │ 1
           │
           │ N
┌──────────▼───────────────────────────────────┐
│            document_versions                  │
├──────────────────────────────────────────────┤
│ id              TEXT PK                       │
│ document_id     TEXT FK → documents           │
│ version         INTEGER                       │
│ content_md      TEXT                          │
│ change_reason   TEXT                          │
│ created_at      TEXT                          │
└──────────────────────────────────────────────┘
```

### Класс `Database`

```
Database(db_path)
│
├── init()           → Создание таблиц, WAL mode, FK constraints
├── close()          → Закрытие соединения
│
├── Организации:
│   ├── save_organization(org: OrganizationData) → str (id)
│   └── get_organization(org_id) → OrganizationData | None
│
├── Сканы:
│   ├── save_scan(org_id, url, result: ScanResult) → str (id)
│   └── get_scan(scan_id) → dict | None
│
├── Отчёты:
│   ├── save_report(scan_id, org_id, report: ComplianceReport) → str (id)
│   └── list_reports(org_id=None, limit=50) → list[dict]
│
└── Документы:
    ├── save_document(doc: dict) → str (id)
    └── get_documents(org_id, doc_type=None) → list[dict]
```

---

## 11. API — структура эндпоинтов

```
FastAPI App (src/api/server.py)
│
├── Lifespan:
│   ├── startup  → Database.init()
│   └── shutdown → Database.close()
│
├── GET  /health
│   └── → {"status": "ok"}
│
├── Сканирование:
│   ├── POST /api/v1/scan
│   │   ├── Body: {url: str, max_pages?: int}
│   │   ├── Действие: SiteScanner.scan(url) → save_scan()
│   │   └── → ScanResponse {scan_id, url, pages, forms, scripts, has_policy, has_ssl}
│   │
│   └── GET  /api/v1/scan/{scan_id}
│       └── → полный ScanResult JSON
│
├── Анализ:
│   ├── POST /api/v1/analyze
│   │   ├── Body: {url: str, organization_id?: str}
│   │   ├── Действие: scan() → analyze() → save_report()
│   │   └── → AnalyzeResponse {report_id, score, risk, violations_count,
│   │                          fine_min, fine_max, summary}
│   │
│   ├── GET  /api/v1/report/{report_id}
│   │   └── → полный ComplianceReport JSON
│   │
│   └── GET  /api/v1/reports?org_id=...&limit=50
│       └── → список отчётов (краткая информация)
│
├── Организации:
│   ├── POST /api/v1/organizations
│   │   ├── Body: OrganizationData (все поля)
│   │   └── → {id, legal_name, inn, created_at}
│   │
│   └── GET  /api/v1/organizations/{org_id}
│       └── → OrganizationData
│
└── Документы:
    ├── GET  /api/v1/documents/types
    │   └── → {types: [{key, title, description, has_template}...]}
    │
    ├── POST /api/v1/documents/generate
    │   ├── Body: {organization_id: str, document_types?: list[str]}
    │   ├── Действие: Generator.generate_full_package() или по списку
    │   └── → {documents: [...], errors: [...]}
    │
    ├── POST /api/v1/documents/generate/public
    │   ├── Body: {organization_id: str}
    │   └── → {documents: [privacy_policy, consent_form, cookie_policy]}
    │
    ├── GET  /api/v1/documents/{org_id}
    │   └── → список всех документов организации
    │
    └── GET  /api/v1/documents/{org_id}/{doc_type}
        └── → конкретный документ (последняя версия)
```

---

## 12. Модели данных

### Иерархия моделей

```
models/
├── scan.py          — результаты сканирования
├── compliance.py    — отчёт о соответствии
├── organization.py  — данные организации
└── legal_update.py  — изменения законодательства

Все модели — Pydantic BaseModel (v2).
Сериализация/десериализация JSON — автоматическая.
```

### Граф зависимостей моделей

```
FormField ──────┐
                ├──> FormInfo ────────────┐
ConsentInfo ────┘                        │
                                         │
CookieInfo ──────────────────────────────┤
CookieBannerInfo ────────────────────────┤
ExternalScript ──────────────────────────┤
PrivacyPolicyInfo ───────────────────────┤──> ScanResult
SSLInfo ─────────────────────────────────┤
PageInfo ────────────────────────────────┘
                                         │
                                         ▼
                                   ComplianceAnalyzer
                                         │
CheckItem ───────────────────────────────┤
Violation ───────────────────────────────┤
FineItem ──> FineEstimate ───────────────┤──> ComplianceReport
                                         │
                                         │
OrganizationData ───────────────> DocumentGenerator ──> Document
                                         │
LegalUpdate ──> legal_context ───────────┘
```

---

## 13. Обработка ошибок и устойчивость

```
Уровень          │ Стратегия                          │ Fallback
─────────────────┼────────────────────────────────────┼──────────────────────
HTTP-краулинг    │ Timeout 30s, пропуск страницы      │ Продолжить с
(Scanner)        │ при ошибке, логирование            │ остальными страницами
─────────────────┼────────────────────────────────────┼──────────────────────
Claude API       │ Exponential backoff:               │ LLMError после
(LLM Client)     │ [1,2,4,8,16,32,60]s               │ исчерпания попыток
                 │ Макс. 300s суммарно                │ (5 минут)
                 │ Retry: 429, 5xx, connection        │
─────────────────┼────────────────────────────────────┼──────────────────────
LLM-анализ       │ Try/except вокруг каждого          │ llm_analysis = None,
политики         │ LLM-вызова в Analyzer              │ отчёт формируется
(Analyzer)       │                                    │ без LLM-части
─────────────────┼────────────────────────────────────┼──────────────────────
Генерация        │ Ошибка одного документа не         │ Частичный результат:
резюме           │ блокирует остальные                │ _fallback_summary()
(Analyzer)       │                                    │
─────────────────┼────────────────────────────────────┼──────────────────────
Генерация        │ Ошибка одного документа не         │ {documents: [...],
документов       │ останавливает генерацию            │  errors: ["doc_type:
(Generator)      │ остальных                          │  error message"]}
─────────────────┼────────────────────────────────────┼──────────────────────
База данных      │ WAL mode (параллельные чтения),    │ RuntimeError
(Storage)        │ FK constraints ON                  │
─────────────────┼────────────────────────────────────┼──────────────────────
API              │ try/except → HTTPException          │ 500 с описанием
(Server)         │ с соответствующим кодом            │ ошибки
```

---

## 14. Конфигурация и окружение

```
.env
├── ANTHROPIC_API_KEY=sk-ant-...      # Обязательный (RuntimeError если нет)
├── CLAUDE_MODEL=claude-sonnet-4-5-20250929  # Модель Claude
├── API_HOST=0.0.0.0                  # Хост FastAPI
├── API_PORT=8000                     # Порт FastAPI
├── DB_PATH=data/compliance.db        # Путь к SQLite
├── MAX_PAGES=100                     # Лимит страниц при сканировании
├── SCAN_TIMEOUT=30                   # Таймаут HTTP-запросов (секунды)
├── CRAWL_DELAY=1.0                   # Задержка между запросами (секунды)
└── LOG_LEVEL=INFO                    # Уровень логирования

Запуск:
  python -m src.main                  # uvicorn с reload=True

  Или напрямую:
  uvicorn src.api.server:app --host 0.0.0.0 --port 8000 --reload
```

---

## Текущий статус реализации

| Модуль | Статус | Файлы |
|--------|--------|-------|
| Scanner | Реализован | `crawler.py`, `detectors.py` |
| Analyzer | Реализован | `analyzer.py` |
| Generator | Реализован | `generator.py`, `prompts.py` |
| LLM Client | Реализован | `client.py`, `prompts.py` |
| Knowledge Base | Реализован | 3 чеклиста, 8 шаблонов, 8 обновлений |
| Storage | Реализован | `database.py` (5 таблиц) |
| API | Реализован | `server.py` (14 эндпоинтов) |
| Models | Реализован | 4 файла моделей |
| Monitor | Заглушка | `__init__.py` (пустой) |
| Updater | Заглушка | `__init__.py` (пустой) |
| DOCX-экспорт | Не реализован | (python-docx в зависимостях) |
| Playwright (SPA) | Не реализован | (не подключён) |
| Тесты | 40+ тестов | 4 файла (detectors, generator, knowledge, legal_updates) |
