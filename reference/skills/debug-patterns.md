# Debug Patterns — compliance152

Паттерны ошибок, найденных и исправленных в проекте.

---

## [2026-02-24] Crawler domain matching bug

### Контекст
`src/scanner/crawler.py` — метод `_is_same_domain` и формирование очереди `to_visit`.

### Проблема #1: точное сравнение netloc ломается на www

**Симптом:** краулер сканирует только 1 страницу на реальных сайтах (tinkoff.ru, sberbank.ru и др.) — все внутренние ссылки отброшены.

**Причина:** сайт сканируется по URL без `www` (`tinkoff.ru`), но в HTML все ссылки абсолютные с `www` (`https://www.tinkoff.ru/...`). Сравнение `"www.tinkoff.ru" == "tinkoff.ru"` → `False` → ссылки молча отбрасываются.

```python
# БЫЛО — ломалось
@staticmethod
def _is_same_domain(url: str, base_domain: str) -> bool:
    return urlparse(url).netloc.lower() == base_domain

# СТАЛО — работает
@staticmethod
def _is_same_domain(url: str, base_domain: str) -> bool:
    def _strip_www(domain: str) -> str:
        return domain[4:] if domain.startswith("www.") else domain
    netloc = urlparse(url).netloc.lower()
    return _strip_www(netloc) == _strip_www(base_domain)
```

**Важно:** субдомены (`sub.example.com`) остаются отдельными — сравнивается только strip_www, а не суффиксы. Это верное поведение.

---

### Проблема #2: очередь to_visit без дедупликации → OOM на больших сайтах

**Симптом:** на сайтах с перекрёстными ссылками (каждая страница ссылается на все остальные) `to_visit` разрастается до O(pages²) записей в памяти.

**Причина:** в момент обнаружения ссылки проверяется только `norm not in visited` — то есть «уже обработан ли». Но URL ещё не обработан, поэтому добавляется снова при каждом упоминании на новой странице.

```python
# БЫЛО — дублировало URL в to_visit
if norm not in visited and self._is_same_domain(norm, base_domain):
    to_visit.append(abs_url)

# СТАЛО — параллельный set для дедупликации очереди
# При инициализации:
to_visit: list[str] = [url]
queued: set[str] = {self._normalize_url(url)}

# В цикле обнаружения ссылок:
if (
    norm not in visited
    and norm not in queued
    and self._is_same_domain(norm, base_domain)
):
    queued.add(norm)
    to_visit.append(abs_url)
```

**Паттерн:** всегда держать два параллельных контейнера для BFS-обхода:
- `visited: set` — уже обработанные
- `queued: set` — уже стоящие в очереди

---

### Верификация

Тест 7 кейсов `_is_same_domain` — все прошли:

| URL | base_domain | Ожидалось | Результат |
|-----|-------------|-----------|-----------|
| `https://www.tinkoff.ru/page` | `tinkoff.ru` | True | OK |
| `https://tinkoff.ru/page` | `www.tinkoff.ru` | True | OK |
| `https://www.tinkoff.ru/page` | `www.tinkoff.ru` | True | OK |
| `https://tinkoff.ru/page` | `tinkoff.ru` | True | OK |
| `https://other.ru/page` | `tinkoff.ru` | False | OK |
| `https://subdomain.tinkoff.ru` | `tinkoff.ru` | False | OK |

Сканирование 4-страничного тест-сайта: до фикса — 1 стр., после — 4 стр., политика найдена.

---

## [2026-02-25] FastAPI UTF-8 JSON — кириллица как ??????

### Контекст
FastAPI + Python standard json library, любой эндпойнт возвращающий кириллицу.

### Проблема
FastAPI по умолчанию сериализует ответы через `json.dumps(..., ensure_ascii=True)`.
Кириллица кодируется как `Сес...`. Некоторые клиенты отображают это как `??????`.

### Фикс — кастомный UTF8JSONResponse

```python
# src/api/server.py

from fastapi.responses import JSONResponse, Response
import json

class UTF8JSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(',', ':'),
        ).encode('utf-8')

app = FastAPI(
    ...
    default_response_class=UTF8JSONResponse,
)
```

### Правило
Всегда добавлять `default_response_class=UTF8JSONResponse` в FastAPI-приложения с русскоязычными данными.

---

## [2026-02-25] Checklist NOT_APPLICABLE вместо пропуска

### Контекст
`src/analyzer/analyzer.py` — методы `_check_forms`, `_check_cookies`, `_check_privacy_policy`.

### Проблема
При отсутствии ключевого элемента (политика ПДн, cookie-баннер, формы) методы делали
ранний `return` после добавления одного пункта-нарушения. Все зависимые пункты чеклиста
молча выпадали из отчёта. Пользователь видел 16 пунктов вместо 35+, без объяснений.

### Антипаттерн

```python
if not pp.found:
    self._add_violation(...)
    return  # ПЛОХО: 16 пунктов выпадают без сообщения
```

### Правильный паттерн — NOT_APPLICABLE с пояснением

```python
if not pp.found:
    self._add_violation(...)
    _SKIPPED = ["POLICY_002", ..., "POLICY_017"]
    for _cid in _SKIPPED:
        self._add_check(
            _cid, CheckCategory.PRIVACY_POLICY, CheckStatus.NOT_APPLICABLE,
            details="Не применимо: политика не найдена на сайте",
        )
    return  # OK: все пункты видны
```

### Принцип
**Пользователь всегда должен видеть полный чеклист.**
Пропущенные проверки = NOT_APPLICABLE с объяснением, а не тишина.
Применимо везде: анализаторы, аудиторы, валидаторы.
