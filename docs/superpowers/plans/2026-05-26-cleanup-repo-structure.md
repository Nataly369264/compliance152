# План реализации: уборка структуры репозитория compliance152

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) или superpowers:executing-plans для пошагового выполнения плана. Шаги используют синтаксис чекбоксов (`- [ ]`).

**Goal:** Привести структуру репозитория к виду из [спеки](../specs/2026-05-26-cleanup-repo-structure-design.md): корень ≤15 объектов, документация в `docs/`, журналы в `docs/journal/`, история в `docs/archive/`, мусор удалён, ссылки актуализированы.

**Architecture:** Чисто структурная уборка — содержимое кода не трогаем. Все переносы делаем через `git mv` (история сохраняется). Удаления подтверждаются перед `git rm`. Правка ссылок — только после показа таблицы и подтверждения пользователя. Smoke-test (pytest, импорт сервера, повторный аудит) запускается один раз перед merge, не после каждого коммита.

**Tech Stack:** Git, bash (POSIX, `git mv`/`git rm`/`grep`/`sed`), pytest.

---

## Stage A — Подготовка ветки и публикация спеки

### Task 0: Создание feature-ветки и страховочного SHA

**Files:**
- Modify: ветка git (создаётся `cleanup/repo-structure`)

- [ ] **Step 0.1: Проверить чистоту рабочего дерева перед стартом**

Run:
```bash
git status
```

Expected: на ветке `feat/qa-google-sheets`. Допустимы untracked: `SKILL.md`, `docs/superpowers/specs/2026-05-26-cleanup-repo-structure-design.md`, `docs/superpowers/plans/2026-05-26-cleanup-repo-structure.md`. Никаких изменённых tracked-файлов.

Если есть что-то ещё — **остановиться** и согласовать с пользователем.

- [ ] **Step 0.2: Зафиксировать страховочный SHA**

Run:
```bash
git rev-parse HEAD
```

Сохранить вывод как `BACKUP_SHA` (выведется в финальном отчёте). Возврат при катастрофе: `git reset --hard <BACKUP_SHA>`.

- [ ] **Step 0.3: Создать ветку `cleanup/repo-structure`**

Run:
```bash
git checkout -b cleanup/repo-structure
git branch --show-current
```

Expected: `cleanup/repo-structure`.

---

### Task 1: Коммит спеки и плана (коммит #1)

**Files:**
- Add: `docs/superpowers/specs/2026-05-26-cleanup-repo-structure-design.md` (уже создан)
- Add: `docs/superpowers/plans/2026-05-26-cleanup-repo-structure.md` (этот файл)

- [ ] **Step 1.1: Добавить spec и план в индекс**

Run:
```bash
git add docs/superpowers/specs/2026-05-26-cleanup-repo-structure-design.md \
        docs/superpowers/plans/2026-05-26-cleanup-repo-structure.md
git status
```

Expected: оба файла в `new file:`.

- [ ] **Step 1.2: Коммит**

Run:
```bash
git commit -m "docs(spec): дизайн и план уборки структуры репозитория

Spec: docs/superpowers/specs/2026-05-26-cleanup-repo-structure-design.md
Plan: docs/superpowers/plans/2026-05-26-cleanup-repo-structure.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --oneline
```

Expected: новый коммит с сообщением `docs(spec): ...`.

---

## Stage B — Механические переносы (4 коммита)

> **Модель для подзадач этого этапа:** `sonnet` (механика — `git mv`).

### Task 2: Архивирование 6 CIM-файлов (коммит #2)

**Files:**
- Move: `ARCHITECTURE.md` → `docs/archive/ARCHITECTURE.md`
- Move: `PLAN.md` → `docs/archive/PLAN.md`
- Move: `HANDOFF.md` → `docs/archive/HANDOFF.md`
- Move: `PROGRESS.md` → `docs/archive/PROGRESS.md`
- Move: `Competitor_Intelligence_Monitor_Passport.md` → `docs/archive/Competitor_Intelligence_Monitor_Passport.md`
- Move: `SESSION_NOTES_2026-03-24.md` → `docs/archive/SESSION_NOTES_2026-03-24.md`

- [ ] **Step 2.1: Создать `docs/archive/` (явно, чтобы первый `git mv` не упал)**

Run:
```bash
mkdir -p docs/archive
ls -d docs/archive
```

Expected: `docs/archive`.

- [ ] **Step 2.2: Перенос 6 файлов одной партией**

Run:
```bash
git mv ARCHITECTURE.md docs/archive/ARCHITECTURE.md
git mv PLAN.md docs/archive/PLAN.md
git mv HANDOFF.md docs/archive/HANDOFF.md
git mv PROGRESS.md docs/archive/PROGRESS.md
git mv Competitor_Intelligence_Monitor_Passport.md docs/archive/Competitor_Intelligence_Monitor_Passport.md
git mv SESSION_NOTES_2026-03-24.md docs/archive/SESSION_NOTES_2026-03-24.md
git status
```

Expected: 6 строк `renamed: <old> -> docs/archive/<old>`.

- [ ] **Step 2.3: Коммит**

Run:
```bash
git commit -m "chore(docs): архивировать документацию модуля Competitor Intelligence Monitor

Артефакты завершённого в марте 2026 модуля переносятся в docs/archive/
для очистки корня. История git сохраняется через git mv.

Файлы:
- ARCHITECTURE.md
- PLAN.md
- HANDOFF.md
- PROGRESS.md
- Competitor_Intelligence_Monitor_Passport.md
- SESSION_NOTES_2026-03-24.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --stat | head -20
```

Expected: коммит, 6 `rename`.

---

### Task 3: Перенос активной документации в `docs/` (коммит #3)

**Files:**
- Move: `PROJECT_PASSPORT.md` → `docs/PROJECT_PASSPORT.md`
- Move: `SCANNER_TEAM_GUIDE.md` → `docs/SCANNER_TEAM_GUIDE.md`
- Move: `CLAUDE_CODE_RULES.md` → `docs/CLAUDE_CODE_RULES.md`
- Move: `GOLDEN_SET_MAPPING.md` → `docs/GOLDEN_SET_MAPPING.md`
- Move + rename: `docs_scanner_logic.md` → `docs/scanner_logic.md`

- [ ] **Step 3.1: Перенос 5 файлов**

Run:
```bash
git mv PROJECT_PASSPORT.md docs/PROJECT_PASSPORT.md
git mv SCANNER_TEAM_GUIDE.md docs/SCANNER_TEAM_GUIDE.md
git mv CLAUDE_CODE_RULES.md docs/CLAUDE_CODE_RULES.md
git mv GOLDEN_SET_MAPPING.md docs/GOLDEN_SET_MAPPING.md
git mv docs_scanner_logic.md docs/scanner_logic.md
git status
```

Expected: 5 строк `renamed:`. Последняя — переименование (`docs_scanner_logic.md -> docs/scanner_logic.md`).

- [ ] **Step 3.2: Коммит**

Run:
```bash
git commit -m "refactor(docs): перенести активную документацию в docs/

Топ-уровневые .md уходят из корня в docs/.
docs_scanner_logic.md переименован в docs/scanner_logic.md.

Файлы:
- PROJECT_PASSPORT.md       → docs/PROJECT_PASSPORT.md
- SCANNER_TEAM_GUIDE.md     → docs/SCANNER_TEAM_GUIDE.md
- CLAUDE_CODE_RULES.md      → docs/CLAUDE_CODE_RULES.md
- GOLDEN_SET_MAPPING.md     → docs/GOLDEN_SET_MAPPING.md
- docs_scanner_logic.md     → docs/scanner_logic.md

Ссылки в CLAUDE.md/README.md обновляются отдельным коммитом
после аудита.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --stat | head -15
```

Expected: коммит, 5 `rename`.

---

### Task 4: Перенос журналов в `docs/journal/` (коммит #4)

**Files:**
- Move: `DECISIONS.md` → `docs/journal/DECISIONS.md`
- Move: `CASES.md` → `docs/journal/CASES.md`
- Move: `PATTERNS.md` → `docs/journal/PATTERNS.md`
- Move + rename: `NEXT_SESSIONS_PLAN.md` → `docs/journal/NEXT_SESSIONS.md`

- [ ] **Step 4.1: Создать `docs/journal/` явно**

Run:
```bash
mkdir -p docs/journal
ls -d docs/journal
```

Expected: `docs/journal`.

- [ ] **Step 4.2: Перенос 4 файлов**

Run:
```bash
git mv DECISIONS.md docs/journal/DECISIONS.md
git mv CASES.md docs/journal/CASES.md
git mv PATTERNS.md docs/journal/PATTERNS.md
git mv NEXT_SESSIONS_PLAN.md docs/journal/NEXT_SESSIONS.md
git status
```

Expected: 4 `renamed:`.

- [ ] **Step 4.3: Коммит**

Run:
```bash
git commit -m "refactor(docs): перенести журналы в docs/journal/

Накопительные журналы (decisions, cases, patterns, next sessions)
уходят из корня в docs/journal/. NEXT_SESSIONS_PLAN.md переименован
в NEXT_SESSIONS.md (короче, без избыточного _PLAN суффикса).

Файлы:
- DECISIONS.md           → docs/journal/DECISIONS.md
- CASES.md               → docs/journal/CASES.md
- PATTERNS.md            → docs/journal/PATTERNS.md
- NEXT_SESSIONS_PLAN.md  → docs/journal/NEXT_SESSIONS.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --stat | head -15
```

Expected: коммит, 4 `rename`.

---

### Task 5: Создать `docs/journal/SESSIONS.md` с политикой ротации (коммит #5)

**Files:**
- Create: `docs/journal/SESSIONS.md`

- [ ] **Step 5.1: Создать файл с шаблоном**

Content (полный текст файла):

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

- [ ] **Step 5.2: Записать файл и проверить**

Run:
```bash
ls -la docs/journal/SESSIONS.md
wc -l docs/journal/SESSIONS.md
```

Expected: файл существует, ~43 строки.

- [ ] **Step 5.3: Коммит**

Run:
```bash
git add docs/journal/SESSIONS.md
git commit -m "docs(journal): завести SESSIONS.md с политикой ротации

Каркас журнала сессий: последние 10 сессий в файле, при превышении
старые уходят в docs/archive/SESSIONS_YYYY.md по году.

Первая запись — текущая сессия уборки структуры.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --oneline
```

Expected: коммит с сообщением `docs(journal): ...`.

---

### Task 6: Удаление мусора + вынос `SKILL.md` (коммит #6)

**Files:**
- Delete (`git rm`): `_scan_umschool_v2.py`, `run_scan_test.py`, `CONTEXT_BUNDLE.md`, `data/scan_umschool_net.json`, `data/scan_umschool_net.txt`, `data/scan_umschool_net_v2.txt`
- Delete (`rm`, был untracked): `SKILL.md`
- Side effect: копирование `SKILL.md` в `~/.claude/skills/uluchshatel-promptov/SKILL.md`

- [ ] **Step 6.1: Проверить, есть ли уже `uluchshatel-promptov` в глобальных скиллах**

Run:
```bash
ls -d ~/.claude/skills/uluchshatel-promptov 2>/dev/null && echo "ALREADY_EXISTS" || echo "NEED_TO_COPY"
```

Expected: одно из двух.

- [ ] **Step 6.2: Если `NEED_TO_COPY` — скопировать**

Run (только если предыдущий шаг вывел `NEED_TO_COPY`):
```bash
mkdir -p ~/.claude/skills/uluchshatel-promptov
cp SKILL.md ~/.claude/skills/uluchshatel-promptov/SKILL.md
ls -la ~/.claude/skills/uluchshatel-promptov/SKILL.md
```

Expected: файл скопирован.

- [ ] **Step 6.3: Удалить `SKILL.md` из корня проекта**

Run:
```bash
rm SKILL.md
ls SKILL.md 2>&1 | grep -i "no such" && echo "OK_DELETED"
```

Expected: `OK_DELETED`.

- [ ] **Step 6.4: Удалить ad-hoc скрипты и сгенерированный bundle**

Run:
```bash
git rm _scan_umschool_v2.py
git rm run_scan_test.py
git rm CONTEXT_BUNDLE.md
git status
```

Expected: 3 `deleted:`.

- [ ] **Step 6.5: Удалить старые dump'ы скана umschool**

Run:
```bash
git rm data/scan_umschool_net.json
git rm data/scan_umschool_net.txt
git rm data/scan_umschool_net_v2.txt
git status
```

Expected: всего 6 `deleted:` (3 предыдущих + 3 этих).

- [ ] **Step 6.6: Коммит**

Run:
```bash
git commit -m "chore: удалить ad-hoc скрипты, dump'ы и сгенерированный CONTEXT_BUNDLE

Удалены файлы, заменённые актуальной инфраструктурой или мусорные:
- _scan_umschool_v2.py     — ad-hoc прогон, заменён tools/run_golden_scan.py
- run_scan_test.py         — ручной запуск сервера, не используется в CI
- CONTEXT_BUNDLE.md        — сгенерированный агрегат, регенерируется
- data/scan_umschool_net.json/txt/v2.txt — старые dump'ы скана

SKILL.md (untracked) перенесён в ~/.claude/skills/uluchshatel-promptov/
и удалён из корня — это глобальный prompt-engineer skill, не часть проекта.

История восстановления: git show <SHA>.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --stat | head -15
```

Expected: коммит, 6 `delete`.

---

## Stage C — Аудит и обновление ссылок

> **Модель для подзадач этого этапа:** `opus` (требует анализа полной таблицы и решения).

### Task 7: Сбор всех markdown-ссылок (этап аудита, без правок)

**Files:**
- Read-only: все `.md`-файлы вне `docs/archive/` и `docs/superpowers/`

- [ ] **Step 7.1: Собрать markdown-ссылки на локальные `.md`-файлы**

Run:
```bash
grep -rn -E '\[[^]]*\]\((?!https?://)[^)]*\.md(#[^)]*)?\)' \
  --include="*.md" \
  --exclude-dir=docs/archive \
  --exclude-dir=docs/superpowers \
  --exclude-dir=.git \
  . | sort
```

Expected: список вида `path:line:[X](Y.md)`. Сохранить вывод в переменную или временный файл `/tmp/md_links.txt` для построения таблицы в Task 8.

- [ ] **Step 7.2: Собрать сырые упоминания имён файлов в коде**

Run:
```bash
grep -rn "docs_scanner_logic\|NEXT_SESSIONS_PLAN\|DECISIONS\.md\|CASES\.md\|PATTERNS\.md\|PROJECT_PASSPORT\|GOLDEN_SET_MAPPING\|SCANNER_TEAM_GUIDE\|CLAUDE_CODE_RULES" \
  --include="*.py" --include="*.toml" --include="*.json" \
  --include="*.yaml" --include="*.sh" --include="*.cfg" \
  --exclude-dir=.git \
  --exclude-dir=docs/archive \
  .
```

Expected: список сырых упоминаний в коде (может быть пустым). Сохранить.

- [ ] **Step 7.3: Собрать ссылки внутри `CLAUDE.md` и `README.md` отдельно (контроль)**

Run:
```bash
echo "=== CLAUDE.md ==="; grep -nE '\.md|docs_scanner_logic|NEXT_SESSIONS' CLAUDE.md
echo "=== README.md ==="; grep -nE '\.md|docs_scanner_logic|NEXT_SESSIONS' README.md
```

Expected: видим все упоминания `.md`-файлов в корневых документах.

---

### Task 8: Построение таблицы битых ссылок и показ пользователю

**Files:**
- Create (временно): `/tmp/link_audit_report.md` или вывод в чат

- [ ] **Step 8.1: По выводам Task 7 построить таблицу**

Для каждой найденной ссылки определить:
- **Файл (где ссылка):** путь, в котором найдена ссылка
- **Строка:** номер строки
- **Старая ссылка:** как написано сейчас
- **Новый путь:** где этот файл лежит после переезда (или `НЕ НАЙДЕНО`, если файл удалён)

Соответствия (карта переездов из спеки §4):

```
ARCHITECTURE.md                              → docs/archive/ARCHITECTURE.md
PLAN.md                                      → docs/archive/PLAN.md
HANDOFF.md                                   → docs/archive/HANDOFF.md
PROGRESS.md                                  → docs/archive/PROGRESS.md
Competitor_Intelligence_Monitor_Passport.md  → docs/archive/Competitor_Intelligence_Monitor_Passport.md
SESSION_NOTES_2026-03-24.md                  → docs/archive/SESSION_NOTES_2026-03-24.md
PROJECT_PASSPORT.md                          → docs/PROJECT_PASSPORT.md
SCANNER_TEAM_GUIDE.md                        → docs/SCANNER_TEAM_GUIDE.md
CLAUDE_CODE_RULES.md                         → docs/CLAUDE_CODE_RULES.md
GOLDEN_SET_MAPPING.md                        → docs/GOLDEN_SET_MAPPING.md
docs_scanner_logic.md                        → docs/scanner_logic.md  (RENAME)
DECISIONS.md                                 → docs/journal/DECISIONS.md
CASES.md                                     → docs/journal/CASES.md
PATTERNS.md                                  → docs/journal/PATTERNS.md
NEXT_SESSIONS_PLAN.md                        → docs/journal/NEXT_SESSIONS.md  (RENAME)
CONTEXT_BUNDLE.md                            → НЕ НАЙДЕНО (удалён)
_scan_umschool_v2.py                         → НЕ НАЙДЕНО (удалён)
run_scan_test.py                             → НЕ НАЙДЕНО (удалён)
ROADMAP.md                                   → НЕ НАЙДЕНО (никогда не существовал в репо)
```

Пересчёт относительных путей:
- ссылка из `CLAUDE.md` (в корне) `[X](DECISIONS.md)` → `[X](docs/journal/DECISIONS.md)`
- ссылка из `docs/PROJECT_PASSPORT.md` `[X](CASES.md)` → `[X](journal/CASES.md)` (относительно `docs/`)
- ссылка из `docs/journal/DECISIONS.md` `[X](CASES.md)` → остаётся `[X](CASES.md)` (тот же каталог)
- ссылка с якорем `[X](DECISIONS.md#dec-008)` → `[X](docs/journal/DECISIONS.md#dec-008)` (якорь сохраняется)

- [ ] **Step 8.2: Вывести две таблицы в чат**

Формат:

```markdown
### Markdown-ссылки

| Файл (где ссылка) | Строка | Старая ссылка | Новый путь |
| ----------------- | ------ | ------------- | ---------- |
| CLAUDE.md         | 86     | DECISIONS.md  | docs/journal/DECISIONS.md |
| ...               | ...    | ...           | ... |

### Сырые ссылки в коде

| Файл | Строка | Сырое упоминание | Действие |
| ---- | ------ | ---------------- | -------- |
| (если нет — «не найдено») | | | |
```

- [ ] **Step 8.3: Запрос подтверждения через AskUserQuestion**

Вопрос вида: «Применить эти правки в коммите #7? (Y / нужны корректировки)».

**Не двигаться дальше без явного подтверждения.**

---

### Task 9: Применение правок ссылок и коммит #7

**Files:**
- Modify: `CLAUDE.md`, `README.md`, и любые другие `.md`/код-файлы, попавшие в таблицу из Task 8

- [ ] **Step 9.1: Применить правки по подтверждённой таблице**

Для каждой строки таблицы — `Edit` или `sed -i` (предпочитаю `Edit` для контролируемости). Использовать точное `old_string`/`new_string`.

Пример (типичный):

```
File: CLAUDE.md
Old: `| `DECISIONS.md`            | Архитектурные решения`
New: `| `docs/journal/DECISIONS.md` | Архитектурные решения`
```

- [ ] **Step 9.2: Контрольный прогон аудита (должен быть пустым)**

Run:
```bash
grep -rnE '\[[^]]*\]\((?!https?://)[^)]*\.md(#[^)]*)?\)' \
  --include="*.md" \
  --exclude-dir=docs/archive \
  --exclude-dir=docs/superpowers \
  --exclude-dir=.git \
  . | while IFS= read -r line; do
    # выделить путь ссылки, проверить существование
    echo "$line"
done | head -50
```

Затем визуально проверить, что все упомянутые `.md` существуют по новым путям.

- [ ] **Step 9.3: `git status` и коммит**

Run:
```bash
git status
git diff --stat
```

Expected: только изменения в файлах, перечисленных в таблице.

```bash
git add -u
git commit -m "docs(refs): обновить markdown-ссылки под новую структуру

После переноса документов в docs/, docs/journal/, docs/archive/
обновлены ссылки в:
- CLAUDE.md (таблица документов проекта)
- README.md
- (другие .md-файлы — см. diff)

Архивные файлы в docs/archive/ не обновляются — исторические артефакты.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git log -1 --stat | head -20
```

Expected: коммит #7.

---

## Stage D — Smoke-test перед merge

> **Модель:** `opus` (анализ выводов, решение go/no-go).

### Task 10: Smoke-test — pytest, импорт сервера, повторный аудит, ls корня

**Files:**
- Read-only

- [ ] **Step 10.1: `pytest -q`**

Run:
```bash
pytest -q 2>&1 | tail -20
```

Expected: `251 passed` (или больше, если за время сессии добавились тесты — главное `0 failed`).

Если падает — **стоп**, откат к `BACKUP_SHA` и разбор.

- [ ] **Step 10.2: Импорт сервера**

Run:
```bash
python -c "from src.api.server import app; print('OK', type(app).__name__)"
```

Expected: `OK FastAPI`.

- [ ] **Step 10.3: Повторный аудит markdown-ссылок (должен быть «чист»)**

Run:
```bash
grep -rnE '\[[^]]*\]\((?!https?://)[^)]*\.md(#[^)]*)?\)' \
  --include="*.md" \
  --exclude-dir=docs/archive \
  --exclude-dir=docs/superpowers \
  --exclude-dir=.git \
  .
```

Для каждой ссылки убедиться, что путь существует: визуально или скриптом:

```bash
grep -rohE '\((?!https?://)[^)]+\.md(#[^)]*)?\)' \
  --include="*.md" \
  --exclude-dir=docs/archive \
  --exclude-dir=docs/superpowers \
  --exclude-dir=.git \
  . | sed -E 's/[()]//g;s/#.*$//' | sort -u | while read p; do
    [[ -e "$p" ]] || echo "MISSING: $p"
done
```

Expected: вывод пустой (нет `MISSING`).

- [ ] **Step 10.4: `ls` корня — проверка ≤15 объектов**

Run:
```bash
ls -1 | wc -l
ls -1
```

Expected: первая строка ≤ 15, во второй — ожидаемый набор (см. §3 спеки).

- [ ] **Step 10.5: Summary коммитов на ветке**

Run:
```bash
git log --oneline cleanup/repo-structure ^feat/qa-google-sheets
```

Expected: 7 коммитов в обратном порядке (новейший сверху):
1. docs(refs): обновить markdown-ссылки под новую структуру
2. chore: удалить ad-hoc скрипты...
3. docs(journal): завести SESSIONS.md с политикой ротации
4. refactor(docs): перенести журналы в docs/journal/
5. refactor(docs): перенести активную документацию в docs/
6. chore(docs): архивировать документацию модуля Competitor Intelligence Monitor
7. docs(spec): дизайн и план уборки структуры репозитория

Если порядок/количество не сходится — разбор перед merge.

---

## Stage E — Финальный отчёт (без коммитов)

### Task 11: Сформировать отчёт по 5 блокам

- [ ] **Step 11.1: Собрать данные**

- BACKUP_SHA (из Task 0)
- Список коммитов с SHA и описанием (из Step 10.5)
- Метрики «до» (из доклада Этапа 1)
- Метрики «после» (из Step 10.4 — ls корня, wc крупных .md)
- Smoke-test (pass/fail каждого пункта Task 10)
- Замечания вне scope (зафиксированные по ходу)

- [ ] **Step 11.2: Выдать отчёт пользователю**

Структура (см. формат вывода в задаче):

1. **Сводка коммитов** — список SHA + однострочное описание.
2. **Метрики до/после** — корень, крупнейший `.md`, число `.md` в корне.
3. **Smoke-test** — что проверила, всё ли работает.
4. **Замечания вне текущей задачи** — ROADMAP.md отсутствует, `reference/skills/debug-patterns.md` изолирован, кириллический XLSX — большой бинарь, и пр.
5. **Что дальше** — варианты завершения: merge в `feat/qa-google-sheets`, PR, проверка в новом чате. **Не пушим без явной команды.**

---

## Self-review (внутренний, перед стартом исполнения)

**Spec coverage:**
- §3 целевая структура → достигается Task 2-6 ✓
- §4 переносы → Task 2 (4.1), Task 3 (4.2), Task 4 (4.3) ✓
- §5 удаления → Task 6 ✓
- §6 вынос SKILL.md → Task 6 ✓
- §7 шаблон SESSIONS.md → Task 5 (Step 5.1 содержит полный текст) ✓
- §8 порядок коммитов → 7 коммитов, точно по §8 ✓
- §9 аудит ссылок → Task 7 (сбор), Task 8 (таблица + подтверждение), Task 9 (правки) ✓
- §9.5 сырые ссылки в коде → Task 7 (Step 7.2) ✓
- §10 smoke-test перед merge → Task 10 ✓
- §11 безопасность (BACKUP_SHA, git mv) → Task 0 (Step 0.2), все переносы через `git mv` ✓
- §12 замечания вне scope → Task 11 (Step 11.2 п.4) ✓

**Placeholder scan:** нет TBD/TODO/«similar to». Все команды конкретны, все имена файлов полные.

**Type consistency:** все имена файлов согласованы между Task 2-9 (например, везде `NEXT_SESSIONS.md`, не `NEXT_SESSIONS_PLAN.md`).

План готов к исполнению.
