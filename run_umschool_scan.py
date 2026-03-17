"""Scan umschool.net and save full report to file."""
import subprocess
import time
import sys
import json
import httpx
from pathlib import Path
from datetime import datetime

TARGET = "https://umschool.net"
MAX_PAGES = 50
OUTPUT_FILE = Path("data/scan_umschool_net.txt")
OUTPUT_FILE.parent.mkdir(exist_ok=True)

lines = []

def log(msg=""):
    print(msg)
    lines.append(msg)

log(f"=== СКАНИРОВАНИЕ {TARGET} ===")
log(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log(f"Максимум страниц: {MAX_PAGES}")
log()

log("Запускаю сервер...")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "src.api.server:app",
     "--host", "0.0.0.0", "--port", "9001"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
time.sleep(12)

try:
    with httpx.Client(trust_env=False, timeout=5) as client:
        client.get("http://127.0.0.1:9001/health")
    log("✓ Сервер запущен")
except Exception as e:
    log(f"✗ Сервер не отвечает: {e}")
    proc.terminate()
    sys.exit(1)

log()
log("─" * 60)
log("ШАГ 1: Сканирование (без LLM)")
log("─" * 60)

try:
    with httpx.Client(trust_env=False, timeout=180) as client:
        r = client.post(
            "http://127.0.0.1:9001/api/v1/scan",
            headers={"Authorization": "Bearer dev"},
            json={"url": TARGET, "max_pages": MAX_PAGES},
        )
    if r.status_code == 200:
        scan = r.json()
        log(f"scan_id:                  {scan['scan_id']}")
        log(f"status:                   {scan['status']}")
        log(f"pages_scanned:            {scan['pages_scanned']}")
        log(f"forms_found:              {scan['forms_found']}")
        log(f"external_scripts_found:   {scan['external_scripts_found']}")
        log(f"privacy_policy_found:     {scan['privacy_policy_found']}")
        log(f"cookie_banner_found:      {scan['cookie_banner_found']}")
        log(f"ssl:                      {scan.get('ssl_valid', 'n/a')}")
        log()
        SCAN_ID = scan["scan_id"]
    else:
        log(f"Ошибка скана: {r.status_code} {r.text[:500]}")
        SCAN_ID = None
except Exception as e:
    log(f"Ошибка запроса: {e}")
    SCAN_ID = None

log()
log("─" * 60)
log("ШАГ 2: Полный анализ с LLM (/analyze)")
log("─" * 60)
log("(может занять 1–3 минуты)")
log()

report = None
try:
    with httpx.Client(trust_env=False, timeout=360) as client:
        r = client.post(
            "http://127.0.0.1:9001/api/v1/analyze",
            headers={"Authorization": "Bearer dev"},
            json={"url": TARGET, "max_pages": MAX_PAGES},
        )
    if r.status_code == 200:
        report = r.json()
    else:
        log(f"Ошибка анализа: {r.status_code} {r.text[:500]}")
except Exception as e:
    log(f"Ошибка запроса: {e}")

if report:
    log(f"report_id:         {report.get('report_id', 'n/a')}")
    log(f"overall_score:     {report.get('overall_score', 'n/a')} / 100")
    log(f"risk_level:        {report.get('risk_level', 'n/a').upper()}")
    log(f"violations_count:  {report.get('violations_count', 'n/a')}")
    log(f"passed_checks:     {report.get('passed_checks', 'n/a')}")
    log(f"failed_checks:     {report.get('failed_checks', 'n/a')}")
    log(f"estimated_fine:    {report.get('estimated_fine_min', 0):,} — {report.get('estimated_fine_max', 0):,} ₽".replace(",", " "))
    log()

    # Получаем полный отчёт через /reports/{id}
    report_id = report.get("report_id")
    full_report = None
    if report_id:
        try:
            with httpx.Client(trust_env=False, timeout=30) as client:
                r2 = client.get(
                    f"http://127.0.0.1:9001/api/v1/reports/{report_id}",
                    headers={"Authorization": "Bearer dev"},
                )
            if r2.status_code == 200:
                full_report = r2.json()
        except Exception as e:
            log(f"Не удалось загрузить полный отчёт: {e}")

    # ── ОГРАНИЧЕНИЯ СКАНИРОВАНИЯ ─────────────────────────────
    limitations = []
    if full_report:
        limitations = full_report.get("scan_limitations", [])
    if limitations:
        log()
        log("⚠️  ОГРАНИЧЕНИЯ СКАНИРОВАНИЯ")
        log("Если сайт использует клиентский рендеринг (React/Vue/Next.js):")
        for note in limitations:
            log(f"  • {note}")

    # ── НАРУШЕНИЯ ────────────────────────────────────────────
    log()
    log("─" * 60)
    log("НАРУШЕНИЯ")
    log("─" * 60)

    violations_src = (full_report or {}).get("violations") or report.get("violations", [])
    if violations_src:
        for v in violations_src:
            sev = v.get("severity", "")
            if isinstance(sev, dict):
                sev = sev.get("value", "")
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
            log(f"{icon} [{sev.upper()}] {v.get('title', '')}")
            log(f"   {v.get('description', '')}")
            if v.get("law_reference"):
                log(f"   Основание: {v['law_reference']}")
            if v.get("recommendation"):
                log(f"   Рекомендация: {v['recommendation']}")
            log()
    else:
        log("Нарушений не обнаружено")

    # ── РЕЗЮМЕ ───────────────────────────────────────────────
    summary_text = report.get("summary", "")
    if not summary_text and full_report:
        summary_text = full_report.get("summary", "")
    if summary_text:
        log()
        log("─" * 60)
        log("РЕЗЮМЕ (LLM)")
        log("─" * 60)
        log(summary_text)

    # ── LLM-АНАЛИЗ ПОЛИТИКИ ──────────────────────────────────
    llm_text = (full_report or {}).get("llm_analysis", "")
    if llm_text:
        log()
        log("─" * 60)
        log("АНАЛИЗ ПОЛИТИКИ КОНФИДЕНЦИАЛЬНОСТИ (LLM)")
        log("─" * 60)
        log(llm_text)

    # ── JSON-дамп ────────────────────────────────────────────
    json_path = Path("data/scan_umschool_net.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_report or report, f, ensure_ascii=False, indent=2, default=str)
    log()
    log(f"Полный JSON сохранён: {json_path}")

log()
log("─" * 60)
log("Останавливаю сервер...")
proc.terminate()
proc.wait(timeout=10)

OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
log(f"Отчёт сохранён: {OUTPUT_FILE}")
