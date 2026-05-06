#!/usr/bin/env python3
"""Диагностика: какие из 7 фиксов реально работают в текущем окружении.

Запуск:
    python3 tools/diagnose_accuracy.py            — без сети, только код и зависимости
    python3 tools/diagnose_accuracy.py <URL>      — + реальный скан указанного сайта

За 30 секунд печатает, что мешает сканеру показать ожидаемый рост точности:
- какие зависимости (playwright, pdfplumber) отсутствуют
- какие фиксы видны в коде, а какие нет (стейл-чекаут)
- статус кэша LLM
- (опционально) реальный скан с фактической глубиной чтения политики
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✅{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}❌{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}━━━ {title} ━━━{RESET}")


# ── 1. Зависимости ──────────────────────────────────────────────────


def check_dependencies() -> dict[str, bool]:
    section("1. ЗАВИСИМОСТИ ОКРУЖЕНИЯ")
    deps = {
        "playwright": "Без неё SPA-фаллбэк (Задача 1) и детекция трекеров через сетевые запросы (Задача 2) не работают",
        "pdfplumber": "Без неё политики в PDF не читаются → Задача 5 (LLM-промпты) не сработает на PDF-сайтах",
        "httpx": "Базовая зависимость статического сканера",
        "bs4": "Базовая зависимость парсинга HTML",
        "playwright.async_api": "Доп. проверка: импортируется ли реально async API",
    }
    results: dict[str, bool] = {}
    for mod, why in deps.items():
        try:
            importlib.import_module(mod)
            ok(f"{mod}")
            results[mod] = True
        except Exception as e:
            fail(f"{mod} — НЕ установлена. {why}.  ({type(e).__name__})")
            results[mod] = False

    if results.get("playwright"):
        # Проверим, скачан ли Chromium для playwright
        try:
            from playwright.async_api import async_playwright

            async def _try_launch() -> bool:
                try:
                    async with async_playwright() as pw:
                        browser = await pw.chromium.launch(headless=True)
                        await browser.close()
                    return True
                except Exception as e:
                    print(f"     {RED}↳ Chromium для playwright не скачан или сломан: {e}{RESET}")
                    print(f"     {YELLOW}   Запустите: playwright install chromium{RESET}")
                    return False

            launched = asyncio.run(_try_launch())
            if launched:
                ok("playwright Chromium запускается")
            else:
                fail("playwright установлен, но Chromium недоступен")
                results["playwright"] = False
        except Exception as e:
            fail(f"Не удалось проверить chromium: {e}")
    return results


# ── 2. Применены ли фиксы (по маркерам в коде) ─────────────────────


def check_code_markers() -> None:
    section("2. ФИКСЫ В КОДЕ — видны ли в чекауте")

    checks = [
        ("Задача 1: SPA-фаллбэк",
         "src/api/server.py", "_SPA_URL_KEYWORDS",
         "Если нет — чекаут не содержит коммита cce5111"),
        ("Задача 2: трекеры через сетевые запросы",
         "src/scanner/playwright_crawler.py", "_build_tracker_scripts",
         "Если нет — чекаут не содержит коммита cce5111"),
        ("Задача 3: GA4 в реестре трекеров",
         "src/scanner/tracker_registry.py", "region1.google-analytics.com",
         "Если нет — чекаут не содержит коммита cce5111"),
        ("Задача 3: Google Fonts в реестре",
         "src/scanner/tracker_registry.py", '"name": "Google Fonts"',
         "Если нет — Google Fonts не детектируется"),
        ("Задача 4: маркетинг-чекбокс",
         "src/scanner/detectors.py", "detect_marketing_checkbox",
         "Если нет — has_marketing_checkbox всегда False"),
        ("Задача 4: новые баннеры (Usercentrics)",
         "src/scanner/detectors.py", "usercentrics",
         "Если нет — баннеры на 5 популярных платформах не детектируются"),
        ("Задача 5: жёсткие LLM-промпты",
         "src/llm/prompts.py", "Молчание документа = нарушение",
         "Если нет — LLM продолжит ставить «соответствует» на молчащих политиках"),
        ("Задача 6: SSL follow_redirects",
         "src/scanner/crawler.py", "follow_redirects=True",
         "Если нет — HTTPS→HTTP редирект не детектируется"),
        ("Фикс psycho-lad: URL-priority вперёд длины",
         "src/scanner/crawler.py", "(cls._url_priority(p.url or \"\"), len(p.text or \"\"))",
         "Если нет — pravila-okazaniya-uslug по-прежнему побеждает politika-konfidencialnosti"),
        ("Штрафы: ч. 10 для FS-001 (а не ч. 1)",
         "knowledge_base/checklists/fine_schedule.json", '"law_reference": "ч. 10 ст. 13.11 КоАП РФ"',
         "Если нет — старая ошибочная привязка к ч. 1"),
        ("Штрафы: новая FS-011 (спец. категории)",
         "knowledge_base/checklists/fine_schedule.json", '"FS-011"',
         "Если нет — пропущена ч. 16 ст. 13.11"),
    ]
    for label, rel_path, marker, hint in checks:
        path = ROOT / rel_path
        if not path.exists():
            fail(f"{label}: файл {rel_path} не найден")
            continue
        try:
            content = path.read_text(encoding="utf-8")
            if marker in content:
                ok(f"{label}")
            else:
                fail(f"{label} — маркер не найден.  {hint}")
        except Exception as e:
            fail(f"{label}: ошибка чтения {rel_path}: {e}")


# ── 3. Поведение в рантайме (без сети) ─────────────────────────────


def check_runtime_behavior() -> None:
    section("3. ПОВЕДЕНИЕ В РАНТАЙМЕ (синтетические сценарии, без сети)")

    # 3.1 SPA-фаллбэк
    try:
        from src.api.server import _is_poor_result, _looks_like_spa
        from src.models.scan import (
            CookieBannerInfo,
            ExternalScript,
            PrivacyPolicyInfo,
            ScanResult,
        )

        spa_result = ScanResult(
            url="https://example.com",
            pages_scanned=3,
            privacy_policy=PrivacyPolicyInfo(found=True),
            cookie_banner=CookieBannerInfo(found=False),
            external_scripts=[
                ExternalScript(
                    url="https://example.com/_next/static/chunks/main.js",
                    page_url="https://example.com",
                    domain="example.com",
                )
            ],
        )
        triggered = _is_poor_result(spa_result)
        if triggered:
            ok("SPA-фаллбэк: триггерится на /_next/ + нет баннера")
        else:
            fail("SPA-фаллбэк: НЕ триггерится — задача 1 не работает")
    except ImportError as e:
        fail(f"SPA-фаллбэк: не удаётся импортировать ({e})")

    # 3.2 Реестр трекеров
    try:
        from src.scanner.tracker_registry import find_trackers_in_scripts

        cases = [
            (["region1.google-analytics.com"], "Google Analytics", "GA4"),
            (["fonts.googleapis.com"], "Google Fonts", "Google Fonts"),
            (["www.google.com"], "Google reCAPTCHA", "reCAPTCHA"),
        ]
        for domains, expected, label in cases:
            found = find_trackers_in_scripts(domains)
            names = [t["name"] for t in found]
            if expected in names:
                ok(f"Реестр: {label} ({domains[0]}) детектируется как «{expected}»")
            else:
                fail(f"Реестр: {label} НЕ детектируется (нашлось: {names or 'ничего'})")
    except Exception as e:
        fail(f"Реестр трекеров: {e}")

    # 3.3 _select_best_policy на psycho-lad
    try:
        from src.scanner.crawler import SiteScanner
        from src.models.scan import PrivacyPolicyInfo

        rules = PrivacyPolicyInfo(
            found=True,
            url="https://psycho-lad.ru/pravila-okazaniya-uslug/",
            text="А" * 25000,
            is_russian=True,
        )
        real_policy = PrivacyPolicyInfo(
            found=True,
            url="https://psycho-lad.ru/politika-konfidencialnosti/",
            text="А" * 5000,
            is_russian=True,
        )
        result = SiteScanner._select_best_policy([rules, real_policy])
        if result is real_policy:
            ok("_select_best_policy: politika-konfidencialnosti побеждает длинные правила")
        else:
            fail(f"_select_best_policy: ВЫБРАНО НЕ ТО — {result.url}")
    except Exception as e:
        fail(f"_select_best_policy: {e}")

    # 3.4 Маркетинг-чекбокс
    try:
        from bs4 import BeautifulSoup
        from src.scanner.detectors import detect_marketing_checkbox

        html = """
        <form>
            <input type="checkbox" name="consent" id="cb1">
            <label for="cb1">Согласен на обработку персональных данных</label>
            <input type="checkbox" name="news" id="cb2">
            <label for="cb2">Хочу получать рассылки</label>
        </form>
        """
        form = BeautifulSoup(html, "lxml").find("form")
        if detect_marketing_checkbox(form):
            ok("detect_marketing_checkbox: находит маркетинговый чекбокс")
        else:
            fail("detect_marketing_checkbox: НЕ находит — задача 4 не работает")
    except Exception as e:
        fail(f"detect_marketing_checkbox: {e}")

    # 3.5 Cookie-баннер: Usercentrics
    try:
        from bs4 import BeautifulSoup
        from src.scanner.detectors import detect_cookie_banner

        html = '<div id="usercentrics-root"><button>Принять</button></div>'
        result = detect_cookie_banner(BeautifulSoup(html, "lxml"))
        if result.found:
            ok("Cookie-баннер: Usercentrics детектируется")
        else:
            fail("Cookie-баннер: Usercentrics НЕ детектируется")
    except Exception as e:
        fail(f"Cookie-баннер: {e}")

    # 3.6 Штрафы: правильные суммы
    try:
        from src.knowledge.loader import get_fine_by_id

        ga_fine = get_fine_by_id("FS-007")
        if ga_fine and "ч. 8" in ga_fine.get("law_reference", ""):
            ok(f"Штраф FS-007 (зарубежные сервисы): {ga_fine['law_reference']}")
        else:
            fail(f"Штраф FS-007 неверный law_reference: {ga_fine.get('law_reference') if ga_fine else 'НЕТ'}")

        spec = get_fine_by_id("FS-011")
        if spec and "ч. 16" in spec.get("law_reference", ""):
            ok(f"Штраф FS-011 (спец. категории, новый): {spec['law_reference']}")
        else:
            fail("Штраф FS-011 не найден или неверный")
    except Exception as e:
        fail(f"Штрафы: {e}")


# ── 4. Кэш LLM ──────────────────────────────────────────────────────


def check_llm_cache() -> None:
    section("4. КЭШ LLM (может маскировать новые промпты)")
    try:
        from src.llm.cache import get_cache

        cache = get_cache()
        stats = cache.stats() if hasattr(cache, "stats") else {}
        size = stats.get("size", "?")
        if isinstance(size, int) and size > 0:
            warn(f"В кэше {size} записей — старые ответы LLM могут маскировать обновлённые промпты Задачи 5.")
            warn("Очистите кэш: POST /api/v1/cache/clear или удалите файл кэша.")
        else:
            ok(f"Кэш LLM пуст или почти пуст ({size} записей) — новые промпты применятся сразу")
    except Exception as e:
        warn(f"Не удалось проверить кэш LLM: {e}")


# ── 5. Реальный скан (опционально) ──────────────────────────────────


async def real_scan(url: str) -> None:
    section(f"5. РЕАЛЬНЫЙ СКАН: {url}")
    try:
        from src.scanner.crawler import SiteScanner

        # Статический скан
        print(f"  {BOLD}Статический скан (httpx + BS4):{RESET}")
        result_static = await SiteScanner(max_pages=10, timeout=15).scan(url)
        _print_scan_summary(result_static, label="static")

        # Playwright скан, если доступен
        try:
            importlib.import_module("playwright")
            print(f"\n  {BOLD}Playwright скан:{RESET}")
            from src.scanner.playwright_crawler import PlaywrightCrawler

            result_pw = await PlaywrightCrawler(max_pages=10, timeout=30).scan(url)
            _print_scan_summary(result_pw, label="playwright")

            print(f"\n  {BOLD}РАЗНИЦА:{RESET}")
            _diff_results(result_static, result_pw)
        except ImportError:
            warn("Playwright не доступен — невозможно сравнить статический и JS-режимы.")
            warn("Если у сайта много контента через JS, это и есть основная причина низкой точности.")

    except Exception as e:
        fail(f"Скан упал: {type(e).__name__}: {e}")


def _print_scan_summary(result, label: str) -> None:
    pp = result.privacy_policy
    cb = result.cookie_banner
    ts = sum(1 for s in result.external_scripts if getattr(s, "service_name", None))
    pol_url = pp.url or "—"
    pol_text_len = len(pp.text or "")
    text_status = (
        f"{GREEN}{pol_text_len} симв.{RESET}" if pol_text_len > 500
        else f"{RED}{pol_text_len} симв. (мало!){RESET}" if pp.found
        else f"{RED}не прочитан{RESET}"
    )
    print(f"    pages_scanned   = {result.pages_scanned}")
    print(f"    privacy_policy  = {pol_url}")
    print(f"      ↳ текст:       {text_status}")
    print(f"      ↳ extraction:  {pp.extraction_method}")
    print(f"    cookie_banner   = {cb.found}")
    print(f"    external_scripts= {len(result.external_scripts)} (с известным сервисом: {ts})")
    if result.scan_limitations:
        print(f"    limitations    = {result.scan_limitations[:2]}")


def _diff_results(static, pw) -> None:
    diffs = []
    if (static.privacy_policy.url or "") != (pw.privacy_policy.url or ""):
        diffs.append(
            f"  - URL политики:    static={static.privacy_policy.url} → pw={pw.privacy_policy.url}"
        )
    static_text = len(static.privacy_policy.text or "")
    pw_text = len(pw.privacy_policy.text or "")
    if abs(static_text - pw_text) > 100:
        diffs.append(f"  - Длина политики:  static={static_text} → pw={pw_text}")
    if static.cookie_banner.found != pw.cookie_banner.found:
        diffs.append(
            f"  - Cookie-баннер:   static={static.cookie_banner.found} → pw={pw.cookie_banner.found}"
        )
    static_t = sum(1 for s in static.external_scripts if getattr(s, "service_name", None))
    pw_t = sum(1 for s in pw.external_scripts if getattr(s, "service_name", None))
    if static_t != pw_t:
        diffs.append(f"  - Трекеров:        static={static_t} → pw={pw_t}")
    if not diffs:
        print(f"    {GREEN}Без существенных отличий — на этом сайте Playwright не помогает.{RESET}")
    else:
        print(f"    {YELLOW}Playwright находит больше — задачи 1 и 2 дают эффект:{RESET}")
        for d in diffs:
            print(d)


# ── main ────────────────────────────────────────────────────────────


def main() -> None:
    print(f"{BOLD}Compliance152 — диагностика точности сканера{RESET}")
    print(f"Чекаут: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")

    deps = check_dependencies()
    check_code_markers()
    check_runtime_behavior()
    check_llm_cache()

    if len(sys.argv) > 1:
        url = sys.argv[1]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            asyncio.run(real_scan(url))
        except KeyboardInterrupt:
            print("\nПрервано пользователем.")

    section("ИТОГ")
    if not deps.get("playwright"):
        print(f"{RED}Главная причина низкой прибавки:{RESET} нет {BOLD}playwright{RESET} —")
        print("  4 из 6 задач плана опираются на JS-рендеринг. Без него:")
        print("    • Задача 1 (SPA-фаллбэк) — фаллбэк падает в _fallback() → 0 эффекта")
        print("    • Задача 2 (трекеры из сетевых запросов) — 0 эффекта")
        print("    • Задача 3 (GA4 / Google Fonts из request_domains) — частично 0")
        print("    • Задача 5 (LLM на политике из JS-сайта) — политика не прочитана")
        print()
        print(f"  {GREEN}Решение:{RESET} pip install playwright && playwright install chromium")
    if not deps.get("pdfplumber"):
        print(f"{RED}Дополнительно:{RESET} нет {BOLD}pdfplumber{RESET} —")
        print("  политики в PDF не читаются → Задача 5 неэффективна на сайтах с PDF-политикой")
        print(f"  {GREEN}Решение:{RESET} pip install pdfplumber")
    if deps.get("playwright") and deps.get("pdfplumber"):
        print(f"{GREEN}Все зависимости на месте.{RESET}")
        print("Если рост точности всё равно низкий — проверьте:")
        print("  1. Совпадает ли запущенный сервис с веткой DIMA (git log -3)")
        print("  2. Очищен ли кэш LLM (POST /api/v1/cache/clear)")
        print("  3. На каком наборе сайтов считается метрика — она не изменится за 1 скан")


if __name__ == "__main__":
    main()
