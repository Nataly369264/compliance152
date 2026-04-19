"""Golden set validation scan runner.

Usage:
    python -m tools.run_golden_scan [URL]

Defaults:
    URL = https://el-ed.ru

Output is always saved to tests/fixtures/golden_runs/ with a dated filename:
    <host>_YYYY-MM-DD.json
If that file already exists, a version suffix is added (_v2, _v3, …).
Existing files are never overwritten.

Runs SiteScanner (httpx, no Playwright) + ComplianceAnalyzer (full LLM)
and saves the raw combined result as JSON. Does NOT modify any src/ files.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.analyzer.analyzer import ComplianceAnalyzer
from src.scanner.crawler import SiteScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("golden_scan")

DEFAULT_URL = "https://el-ed.ru"
GOLDEN_RUNS_DIR = Path("tests/fixtures/golden_runs")


def _resolve_output_path(url: str) -> Path:
    """Return a non-conflicting path in GOLDEN_RUNS_DIR.

    Format: <host>_YYYY-MM-DD.json
    If that file exists: <host>_YYYY-MM-DD_v2.json, _v3, … until a free slot.
    Never overwrites an existing file.
    """
    normalized = url if url.startswith(("http://", "https://")) else "https://" + url
    host = (urlparse(normalized).hostname or "unknown").split(".")[0]
    date_str = datetime.now().strftime("%Y-%m-%d")
    candidate = GOLDEN_RUNS_DIR / f"{host}_{date_str}.json"
    if not candidate.exists():
        return candidate
    for v in range(2, 1000):
        candidate = GOLDEN_RUNS_DIR / f"{host}_{date_str}_v{v}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"No free slot for golden run: {host} {date_str}")


async def run(url: str, output_path: Path) -> None:
    logger.info("=== Golden scan started: %s ===", url)

    # Step 1: crawl with SiteScanner (httpx, no Playwright)
    logger.info("Step 1/2: crawling with SiteScanner...")
    scanner = SiteScanner(max_pages=50, timeout=30, crawl_delay=1.0)
    scan_result = await scanner.scan(url)

    logger.info(
        "Crawl complete: %d pages, %d forms, %d scripts, %d cookies, %d errors",
        scan_result.pages_scanned,
        len(scan_result.forms),
        len(scan_result.external_scripts),
        len(scan_result.cookies),
        len(scan_result.errors),
    )

    # Step 2: analyze with full LLM pipeline
    logger.info("Step 2/2: running ComplianceAnalyzer (LLM enabled)...")
    analyzer = ComplianceAnalyzer(scan_result, enable_web_verification=True)
    report = await analyzer.analyze()

    logger.info(
        "Analysis complete: score=%d%%, total=%d, passed=%d, failed=%d, warnings=%d",
        report.overall_score,
        report.total_checks,
        report.passed_checks,
        report.failed_checks,
        report.warnings,
    )

    # Serialize both scan_result and report into one JSON
    output = {
        "run_info": {
            "url": url,
            "run_at": datetime.utcnow().isoformat() + "Z",
            "scanner": "SiteScanner (httpx)",
            "llm_enabled": True,
        },
        "scan_result": {
            "url": scan_result.url,
            "pages_scanned": scan_result.pages_scanned,
            "errors": scan_result.errors,
            "privacy_policy": {
                "found": scan_result.privacy_policy.found,
                "url": scan_result.privacy_policy.url,
                "in_footer": scan_result.privacy_policy.in_footer,
                "content_length": scan_result.privacy_policy.content_length,
                "has_operator_name": scan_result.privacy_policy.has_operator_name,
                "has_inn_ogrn": scan_result.privacy_policy.has_inn_ogrn,
                "has_responsible_person": scan_result.privacy_policy.has_responsible_person,
                "has_data_categories": scan_result.privacy_policy.has_data_categories,
                "has_purposes": scan_result.privacy_policy.has_purposes,
                "has_legal_basis": scan_result.privacy_policy.has_legal_basis,
                "has_retention_periods": scan_result.privacy_policy.has_retention_periods,
                "has_subject_rights": scan_result.privacy_policy.has_subject_rights,
                "has_rights_procedure": scan_result.privacy_policy.has_rights_procedure,
                "has_cross_border_info": scan_result.privacy_policy.has_cross_border_info,
                "has_security_measures": scan_result.privacy_policy.has_security_measures,
                "has_cookie_info": scan_result.privacy_policy.has_cookie_info,
                "has_localization_statement": scan_result.privacy_policy.has_localization_statement,
                "has_date": scan_result.privacy_policy.has_date,
                "is_russian": scan_result.privacy_policy.is_russian,
            },
            "ssl_info": {
                "has_ssl": scan_result.ssl_info.has_ssl,
                "certificate_valid": scan_result.ssl_info.certificate_valid,
            },
            "cookie_banner": {
                "found": scan_result.cookie_banner.found,
                "has_accept_button": scan_result.cookie_banner.has_accept_button,
                "has_decline_button": scan_result.cookie_banner.has_decline_button,
                "has_category_choice": scan_result.cookie_banner.has_category_choice,
                "has_cookie_policy_link": scan_result.cookie_banner.has_cookie_policy_link,
                "analytics_before_consent": scan_result.cookie_banner.analytics_before_consent,
            },
            "forms": [
                {
                    "page_url": f.page_url,
                    "collects_personal_data": f.collects_personal_data,
                    "personal_data_fields": f.personal_data_fields,
                    "has_consent_checkbox": f.has_consent_checkbox,
                    "consent_checkbox_prechecked": f.consent_checkbox_prechecked,
                    "has_privacy_link": f.has_privacy_link,
                    "has_marketing_checkbox": f.has_marketing_checkbox,
                }
                for f in scan_result.forms
            ],
            "external_scripts": [
                {
                    "url": s.url,
                    "domain": s.domain,
                    "is_prohibited": s.is_prohibited,
                    "service_name": s.service_name,
                    "script_type": s.script_type,
                }
                for s in scan_result.external_scripts
            ],
            "cookies": [
                {"name": c.name, "domain": c.domain, "secure": c.secure, "category": c.category}
                for c in scan_result.cookies
            ],
            "pages": [
                {
                    "url": p.url,
                    "status_code": p.status_code,
                    "title": p.title,
                    "has_privacy_link_in_footer": p.has_privacy_link_in_footer,
                    "forms_count": p.forms_count,
                    "external_scripts_count": p.external_scripts_count,
                }
                for p in scan_result.pages
            ],
        },
        "compliance_report": {
            "id": report.id,
            "overall_score": report.overall_score,
            "risk_level": report.risk_level,
            "total_checks": report.total_checks,
            "passed_checks": report.passed_checks,
            "failed_checks": report.failed_checks,
            "warnings": report.warnings,
            "scan_limitations": report.scan_limitations,
            "checklist": [
                {
                    "id": c.id,
                    "category": c.category,
                    "title": c.title,
                    "status": c.status,
                    "severity": c.severity,
                    "details": c.details,
                }
                for c in report.checklist
            ],
            "violations": [
                {
                    "check_id": v.check_id,
                    "title": v.title,
                    "severity": v.severity,
                    "category": v.category,
                    "page_url": v.page_url,
                }
                for v in report.violations
            ],
            "fine_estimate": {
                "min_total": report.fine_estimate.min_total,
                "max_total": report.fine_estimate.max_total,
            },
            "llm_analysis": report.llm_analysis,
            "summary": report.summary,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Saved to: %s", output_path)
    logger.info("=== Done ===")


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    output_path = _resolve_output_path(url)
    asyncio.run(run(url, output_path))


if __name__ == "__main__":
    main()
