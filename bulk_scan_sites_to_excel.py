"""Bulk scan of 20 sites → fill column E of Лист 3 in Золотой_набор_v3.xlsx."""
import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import openpyxl

from src.scanner.crawler import SiteScanner

EXCEL_PATH = "/Users/dianayagubov/Downloads/Золотой_набор_v3.xlsx"

SITES = [
    "skyeng.ru",
    "geekbrains.ru",
    "foxford.ru",
    "skillbox.ru",
    "netology.ru",
    "stepik.org",
    "coursera.org/ru",
    "uchi.ru",
    "учи.рф",
    "rosuchebnik.ru",
    "profil-edu.ru",
    "egecrm.ru",
    "repetitors.info",
    "100ballov.ru",
    "учитель.com",
    "videouroki.net",
    "infourok.ru",
    "yandex.ru/tutor",
    "sferum.ru",
    "school-primer.ru",
]

# Each site has exactly 10 checks in this order (matches Sheet 3 rows)
CHECKS_ORDER = [
    "Политика конфиденциальности",
    "Cookie-баннер",
    "Форма сбора данных",
    "Согласие на обработку ПД",
    "Ссылка на политику в форме",
    "Оператор ПД указан",
    "Цели обработки указаны",
    "Срок хранения данных",
    "Контакты для отзыва согласия",
    "SSL / HTTPS",
]


def map_scan_to_checks(result) -> list[str]:
    """Convert ScanResult to 10 Да/Нет/PDF/JS/? values."""
    pp = result.privacy_policy
    cb = result.cookie_banner
    pd_forms = [f for f in result.forms if f.collects_personal_data]

    # 1. Политика конфиденциальности
    if pp.found and not pp.text:
        policy = "PDF"
    elif pp.found:
        policy = "Да"
    else:
        policy = "Нет"

    # 2. Cookie-баннер
    # Static scanner can't detect JS-rendered banners — mark as Нет
    cookie = "Да" if cb.found else "Нет"

    # 3. Форма сбора данных
    form_exists = "Да" if pd_forms else "Нет"

    # 4. Согласие на обработку ПД
    if not pd_forms:
        consent = "?"  # no PD forms found — not applicable
    else:
        consent = "Да" if all(f.has_consent_checkbox for f in pd_forms) else "Нет"

    # 5. Ссылка на политику в форме
    if not pd_forms:
        link_in_form = "?"
    else:
        link_in_form = "Да" if all(f.has_privacy_link for f in pd_forms) else "Нет"

    # 6–9: policy content (regex-extracted in crawler, not LLM)
    if not pp.found:
        operator = purposes = retention = contacts = "Нет"
    elif not pp.text:
        # Policy found but no extractable text (PDF or empty)
        operator = purposes = retention = contacts = "?"
    else:
        operator = "Да" if pp.has_operator_name else "Нет"
        purposes = "Да" if pp.has_purposes else "Нет"
        retention = "Да" if pp.has_retention_periods else "Нет"
        contacts = "Да" if pp.has_responsible_person else "Нет"

    # 10. SSL / HTTPS
    ssl = "Да" if result.ssl_info.has_ssl else "Нет"

    return [policy, cookie, form_exists, consent, link_in_form,
            operator, purposes, retention, contacts, ssl]


async def main():
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws3 = wb.worksheets[2]  # Лист 3 — Результаты сканера

    scanner = SiteScanner(max_pages=5, timeout=20, crawl_delay=0.5)

    for site_idx, site in enumerate(SITES):
        print(f"\n[{site_idx + 1:02d}/20] Scanning {site} ...", flush=True)
        try:
            result = await scanner.scan(site)
            values = map_scan_to_checks(result)
            print(f"       pages={result.pages_scanned}  "
                  f"privacy={result.privacy_policy.found}  "
                  f"cookie_banner={result.cookie_banner.found}  "
                  f"ssl={result.ssl_info.has_ssl}")
        except Exception as exc:
            print(f"       ERROR: {exc}")
            values = ["?"] * 10

        # Sheet 3: site 1 → rows 3–12, site 2 → rows 13–22, etc.
        start_row = 3 + site_idx * 10
        for i, val in enumerate(values):
            ws3.cell(row=start_row + i, column=5, value=val)

        print(f"       → {dict(zip(CHECKS_ORDER, values))}")

        # Save after each site so progress isn't lost on error
        wb.save(EXCEL_PATH)

    print("\nDone! File saved to", EXCEL_PATH)


if __name__ == "__main__":
    asyncio.run(main())
