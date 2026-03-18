"""Direct scan of umschool.net — Stage 1-5 fixes applied (v2)."""
import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import httpx
from bs4 import BeautifulSoup

from src.scanner.crawler import SiteScanner
from src.analyzer.analyzer import analyze_site
from src.scanner.detectors import detect_footer_privacy_link


async def _probe_privacy_pdf(base_url: str) -> dict:
    """Check if the privacy policy link in footer points to a PDF."""
    result = {"pdf_url": None, "pdf_found": False}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15,
                                     headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(base_url)
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "privacy" in href.lower() or "конфиденц" in a.get_text(strip=True).lower():
                    abs_url = href if href.startswith("http") else base_url.rstrip("/") + href
                    probe = await client.get(abs_url)
                    ct = probe.headers.get("content-type", "")
                    result["pdf_url"] = abs_url
                    result["pdf_found"] = "pdf" in ct.lower()
                    break
    except Exception:
        pass
    return result


async def main():
    url = "https://umschool.net"
    print(f"Scanning {url} with v2 scanner (stages 1–5 applied) ...")

    # Probe for PDF privacy policy
    pdf_info = await _probe_privacy_pdf(url)

    scanner = SiteScanner(max_pages=8)
    result = await scanner.scan(url)

    print(f"\nPages scanned : {result.pages_scanned}")
    for p in result.pages:
        print(f"  {p.url}")
    print(f"Forms found   : {len(result.forms)}")
    print(f"Privacy policy: found={result.privacy_policy.found}  url={result.privacy_policy.url}")
    if pdf_info["pdf_found"]:
        print(f"  [NOTE] PDF privacy policy detected at: {pdf_info['pdf_url']}")
        print(f"         Static HTML scanner cannot analyze PDF content.")
    print(f"Cookie banner : found={result.cookie_banner.found}")
    print(f"  has_accept={result.cookie_banner.has_accept_button}  "
          f"has_decline={result.cookie_banner.has_decline_button}")
    print()

    print("Analyzing ...")
    report = await analyze_site(result)

    lines = []
    lines.append("=" * 70)
    lines.append(f"ОТЧЁТ: {url}")
    lines.append(f"Дата: 2026-03-17  |  Версия: v2 (этапы 1–5 применены)")
    lines.append("=" * 70)
    lines.append(f"Общий балл    : {report.overall_score}/100")
    lines.append(f"Уровень риска : {report.risk_level.value}")
    lines.append(f"Всего проверок: {report.total_checks}")
    lines.append(f"PASS          : {report.passed_checks}")
    lines.append(f"FAIL/WARN     : {report.failed_checks}")
    lines.append(f"Нарушений     : {len(report.violations)}")
    lines.append(f"Критических   : {sum(1 for v in report.violations if v.severity.value == 'critical')}")
    lines.append("")
    lines.append("ПРИМЕЧАНИЯ К СКАНИРОВАНИЮ:")
    lines.append(f"  Страниц посещено: {result.pages_scanned}")
    if pdf_info["pdf_found"]:
        lines.append(f"  ! Политика ПДн опубликована как PDF ({pdf_info['pdf_url']})")
        lines.append(f"    Содержимое PDF не анализируется — проверки POLICY_003-016 не применимы.")
    if not result.cookie_banner.found:
        lines.append(f"  ! Cookie-баннер не обнаружен статичным парсером.")
        lines.append(f"    Умскул использует Next.js (SPA) — баннер может монтироваться JavaScript.")
        lines.append(f"    Рекомендуется ручная проверка в браузере.")
    lines.append("")
    lines.append("НАРУШЕНИЯ:")
    lines.append("-" * 70)

    if not report.violations:
        lines.append("  Нарушений не обнаружено.")
    else:
        for v in report.violations:
            sev = v.severity.value.upper()
            lines.append(f"[{sev:8s}] {v.check_id}  {v.title}")
            lines.append(f"           {v.description}")
            lines.append(f"           Статья: {v.law_reference or '—'}")
            lines.append(f"           Рек.: {v.recommendation}")
            if v.page_url:
                lines.append(f"           Стр.: {v.page_url}")
            lines.append("")

    lines.append("=" * 70)
    lines.append("ЧЕК-ЛИСТ:")
    lines.append("-" * 70)
    for c in report.checklist:
        marker = "✓" if c.status.value == "pass" else ("?" if c.status.value in ("warning", "not_applicable", "manual_check") else "✗")
        lines.append(f"  {marker} [{c.status.value:14s}]  {c.id:12s}  {c.title}")
        if c.details:
            lines.append(f"                              {c.details}")

    output = "\n".join(lines)
    print(output)

    out_path = "data/scan_umschool_net_v2.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\nSaved to {out_path}")


asyncio.run(main())
