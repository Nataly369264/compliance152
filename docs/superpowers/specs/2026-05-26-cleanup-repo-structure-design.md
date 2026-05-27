# Дизайн: уборка структуры репозитория compliance152

**Дата:** 2026-05-26
**Ветка реализации:** `cleanup/repo-structure` (от `feat/qa-google-sheets`)
**Тип задачи:** структурная уборка + введение каркаса под рост (журналы, архив)

---

## 1. Цель

Привести структуру репозитория в состояние, в котором:

- корень читается за 2 секунды (≤15 объектов вместо 38);
- актуальная документация лежит в `docs/`, журналы — в `docs/journal/`, история — в `docs/archive/`;
- мусор (ad-hoc скрипты, сгенерированные дампы, untracked файлы) удалён;
- есть пустой каркас `SESSIONS.md` с политикой ротации под рост.

## 2. Не цели

- Не правим содержимое кода (`src/`, `tests/`, `scripts/`, `tools/`).
- Не рефакторим документы (только перемещаем и обновляем ссылки).
- Не меняем имена закоммиченных бинарных файлов с кириллицей.
- Не трогаем `reference/skills/` и `tools/__pycache__/`.

## 3. Целевая структура

```
/                                       ← ≤15 объектов
├── README.md
├── CLAUDE.md
├── .env, .env.example, .gitignore
├── pyproject.toml, requirements.txt
├── сканер_152фз_v7_27.04.xlsx           (закоммиченный эталон, не трогаем)
├── src/, tests/, scripts/, tools/
├── config/, data/, knowledge_base/
└── docs/
    ├── PROJECT_PASSPORT.md              (← корень)
    ├── SCANNER_TEAM_GUIDE.md            (← корень)
    ├── CLAUDE_CODE_RULES.md             (← корень)
    ├── GOLDEN_SET_MAPPING.md            (← корень)
    ├── TESTING_GUIDE.md                 (уже здесь)
    ├── scanner_logic.md                 (← docs_scanner_logic.md, переименование)
    ├── superpowers/specs/               (этот файл и будущие спеки)
    ├── journal/
    │   ├── SESSIONS.md                  (новый, с политикой ротации)
    │   ├── DECISIONS.md                 (← корень)
    │   ├── CASES.md                     (← корень)
    │   ├── PATTERNS.md                  (← корень)
    │   └── NEXT_SESSIONS.md             (← NEXT_SESSIONS_PLAN.md)
    └── archive/
        ├── ARCHITECTURE.md
        ├── PLAN.md
        ├── HANDOFF.md
        ├── PROGRESS.md
        ├── Competitor_Intelligence_Monitor_Passport.md
        └── SESSION_NOTES_2026-03-24.md
```

## 4. Переносы (через `git mv`, история сохраняется)

### 4.1. В `docs/archive/` (мартовские артефакты завершённого модуля CIM)

| Откуда | Куда |
| --- | --- |
| `ARCHITECTURE.md` | `docs/archive/ARCHITECTURE.md` |
| `PLAN.md` | `docs/archive/PLAN.md` |
| `HANDOFF.md` | `docs/archive/HANDOFF.md` |
| `PROGRESS.md` | `docs/archive/PROGRESS.md` |
| `Competitor_Intelligence_Monitor_Passport.md` | `docs/archive/Competitor_Intelligence_Monitor_Passport.md` |
| `SESSION_NOTES_2026-03-24.md` | `docs/archive/SESSION_NOTES_2026-03-24.md` |

### 4.2. В `docs/` (активная верхнеуровневая документация)

| Откуда | Куда |
| --- | --- |
| `PROJECT_PASSPORT.md` | `docs/PROJECT_PASSPORT.md` |
| `SCANNER_TEAM_GUIDE.md` | `docs/SCANNER_TEAM_GUIDE.md` |
| `CLAUDE_CODE_RULES.md` | `docs/CLAUDE_CODE_RULES.md` |
| `GOLDEN_SET_MAPPING.md` | `docs/GOLDEN_SET_MAPPING.md` |
| `docs_scanner_logic.md` | `docs/scanner_logic.md` (переименование) |

### 4.3. В `docs/journal/` (накопительные журналы)

| Откуда | Куда |
| --- | --- |
| `DECISIONS.md` | `docs/journal/DECISIONS.md` |
| `CASES.md` | `docs/journal/CASES.md` |
| `PATTERNS.md` | `docs/journal/PATTERNS.md` |
| `NEXT_SESSIONS_PLAN.md` | `docs/journal/NEXT_SESSIONS.md` (переименование) |

## 5. Удаления

Каждое удаление подтверждается отдельно перед коммитом. История остаётся в git.

| Файл | Причина |
| --- | --- |
| `_scan_umschool_v2.py` | ad-hoc прогон, заменён `tools/run_golden_scan.py` |
| `run_scan_test.py` | ручной запуск сервера, не используется в CI |
| `CONTEXT_BUNDLE.md` | сгенерированный агрегат, регенерируется |
| `data/scan_umschool_net.json` | старый dump скана, мусор |
| `data/scan_umschool_net.txt` | старый dump скана, мусор |
| `data/scan_umschool_net_v2.txt` | старый dump скана, мусор |

## 6. Вынос за пределы проекта

`SKILL.md` (untracked, prompt-engineer skill, не часть проекта):

1. Проверить наличие в `~/.claude/skills/`.
2. Если нет — скопировать как `~/.claude/skills/uluchshatel-promptov/SKILL.md`.
3. Удалить `SKILL.md` из корня проекта (untracked → просто `rm`).

## 7. Новый файл: `docs/journal/SESSIONS.md`

Шаблон-каркас:

```markdown
# Журнал сессий

Хронология сессий разработки compliance152. Свежие сверху.

## Политика ротации

- В этом файле — **последние 10 сессий**.
- Когда счётчик превышает 10 — самые старые выносятся в
  `docs/archive/SESSIONS_YYYY.md` (по году).
- Каждая запись — компактный блок: Дата / Сделано / Оставшиеся
  задачи / Контекст для следующей сессии.

## Шаблон записи

### YYYY-MM-DD — <короткое название>

**Сделано:**
- ...

**Оставшиеся задачи:**
- ...

**Контекст для следующей сессии:**
- ...

---

## Записи

### 2026-05-26 — Уборка структуры репозитория

**Сделано:**
- Перенесена документация в `docs/`, журналы — в `docs/journal/`,
  устаревшие артефакты — в `docs/archive/`.
- Удалены ad-hoc скрипты `_scan_umschool_v2.py`, `run_scan_test.py`,
  сгенерированный `CONTEXT_BUNDLE.md` и старые dump'ы в `data/`.
- Заведён каркас `SESSIONS.md` с политикой ротации.
- Обновлены ссылки в `CLAUDE.md` и `README.md` под новую структуру.

**Оставшиеся задачи:**
- В `CLAUDE.md` ссылка на `ROADMAP.md`, которого нет в репо
  (зафиксировано в «Замечаниях вне scope»).
- `reference/skills/debug-patterns.md` лежит изолированно;
  решение по месту — в следующую сессию.

**Контекст для следующей сессии:**
- Новые сессионные записи добавлять сюда сверху.
- При достижении 11-й записи перенести самую старую в
  `docs/archive/SESSIONS_2026.md`.
```

## 8. Порядок коммитов

Все коммиты — на ветке `cleanup/repo-structure`, conventional commits, на русском.
Папки `docs/journal/` и `docs/archive/` создаются автоматически при первом `git mv` — отдельный коммит «создать каркас» не нужен.

| № | Команда / суть | Сообщение коммита |
| - | --- | --- |
| 1 | `git add docs/superpowers/specs/2026-05-26-cleanup-repo-structure-design.md` | `docs(spec): дизайн уборки структуры репозитория` |
| 2 | `git mv` 6 файлов из §4.1 | `chore(docs): архивировать документацию модуля Competitor Intelligence Monitor` |
| 3 | `git mv` 5 файлов из §4.2 | `refactor(docs): перенести активную документацию в docs/` |
| 4 | `git mv` 4 файла из §4.3 | `refactor(docs): перенести журналы в docs/journal/` |
| 5 | `Write docs/journal/SESSIONS.md` | `docs(journal): завести SESSIONS.md с политикой ротации` |
| 6 | `git rm` 5 файлов + `rm SKILL.md` (untracked) | `chore: удалить ad-hoc скрипты, dump'ы и сгенерированный CONTEXT_BUNDLE` |
| 7 | См. §9 — аудит и обновление ссылок | `docs(refs): обновить markdown-ссылки под новую структуру` |

## 9. Этап аудита ссылок (между коммитом #6 и #7)

**Не правим вслепую — сначала аудит и подтверждение.**

### 9.1. Сбор ссылок

Регулярка для поиска markdown-ссылок на локальные `.md`-файлы (исключая http/https):

```
\[[^\]]*\]\((?!https?://)[^)]*\.md(#[^)]*)?\)
```

Источники для поиска (после переездов §4):

- `CLAUDE.md`, `README.md` (корень)
- все `docs/**/*.md` (включая перенесённые)
- `src/**/*.md`, `tests/**/*.md`, `knowledge_base/**/*.md`, `config/**/*.md`, `scripts/**/*.md`, `tools/**/*.md`
- любые другие `.md`, не входящие в `docs/archive/` (архив не обновляем — это исторические артефакты)

### 9.2. Учитываемые варианты

- ссылки с якорями: `[X](file.md#раздел)` — якорь сохраняется при переезде, путь корректируется;
- относительные пути с `../`: пересчитываются относительно новой позиции файла;
- разный регистр (`PATTERNS.md` vs `patterns.md`) — приводим к фактическому имени файла;
- ссылки внутри `CLAUDE.md` и `README.md` (которые остаются в корне).

### 9.3. Формат отчёта (выдаётся до правок, ждём подтверждения)

Таблица:

```
| Файл (где ссылка) | Строка | Старая ссылка | Новый путь / НЕ НАЙДЕНО |
| ----------------- | ------ | ------------- | ----------------------- |
| CLAUDE.md         | 86     | DECISIONS.md  | docs/journal/DECISIONS.md |
| docs/PROJECT_PASSPORT.md | 142 | CASES.md  | docs/journal/CASES.md |
| ...               | ...    | ...           | ...                     |
```

Пользователь подтверждает таблицу (или отмечает корректировки) → только тогда правки в коммите #7.

### 9.4. Архив исключён

Файлы в `docs/archive/` не правим — это исторические артефакты, в их ссылках остаются исходные пути. Внутренние ссылки между ними друг на друга остаются битыми по замыслу.

### 9.5. Сырые упоминания имён файлов в коде (не только в `.md`)

Помимо markdown-ссылок проходим `grep` по сырым именам перенесённых и переименованных файлов во всех типах исходников. Особое внимание двум переименованиям:

- `docs_scanner_logic.md` → `scanner_logic.md`
- `NEXT_SESSIONS_PLAN.md` → `NEXT_SESSIONS.md`

Команда:

```
grep -rn "docs_scanner_logic\|NEXT_SESSIONS_PLAN\|DECISIONS\.md\|CASES\.md\|PATTERNS\.md\|PROJECT_PASSPORT\|GOLDEN_SET_MAPPING\|SCANNER_TEAM_GUIDE\|CLAUDE_CODE_RULES" \
  --include="*.py" --include="*.toml" --include="*.json" \
  --include="*.yaml" --include="*.sh" --include="*.cfg" .
```

Найденное добавляется в отчёт §9.3 отдельной секцией **«Сырые ссылки в коде»** и показывается пользователю **до** правок. Архивные `docs/archive/**` исключаются.

## 10. Smoke-test перед merge (один раз, не после каждого коммита)

1. `pytest -q` → ожидается `251 passed`.
2. `python -c "from src.api.server import app"` → импорт сервера без ошибок.
3. Повторный аудит ссылок (§9.1) → нет «НЕ НАЙДЕНО» вне `docs/archive/`.
4. `ls` корня → ≤15 объектов.

Если хотя бы один пункт падает — стоп, откат к страховочному SHA, разбор.

## 11. Безопасность

- Перед стартом — фиксируем страховочный SHA: `git rev-parse HEAD` → запоминается, попадает в финальный отчёт.
- Все переносы — только `git mv` (история файлов).
- Не пушим в `origin` без отдельной команды.
- Удаления — каждое подтверждается перед `git rm`.
- Ветка `cleanup/repo-structure` создаётся от `feat/qa-google-sheets`.

## 12. Замечания вне scope (не правим, фиксируем в финальном отчёте)

- `CLAUDE.md` содержит ссылку на `ROADMAP.md`, которого нет в репо.
- `reference/skills/debug-patterns.md` лежит изолированно в `reference/skills/`; стоит решить вопрос о месте этого файла в следующую сессию.
- Имена кириллических файлов (`сканер_152фз_v7_27.04.xlsx`) могут давать проблемы в некоторых toolchain — но не в скоупе.
- Закоммиченный 276 KB XLSX-эталон — большой бинарь в git; вопрос git-lfs выносится за рамки.

## 13. Open questions — нет

Все решения зафиксированы. Если в ходе реализации появится новое — выносим в финальный отчёт под «Замечания вне scope» и/или в `SESSIONS.md`.
