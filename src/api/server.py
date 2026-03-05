from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.analyzer.analyzer import analyze_site
from src.export.docx_converter import MarkdownToDocxConverter
from src.export.pdf_converter import MarkdownToPdfConverter, create_merged_pdf
from src.generator.generator import generate_documents
from src.generator.prompts import DOCUMENT_TYPES, FULL_PACKAGE, PUBLIC_DOCUMENTS
from src.llm.cache import get_cache
from src.models.organization import OrganizationData
from src.monitor.monitor import run_monitoring_cycle
from src.scanner.crawler import SiteScanner
from src.scheduler.jobs import create_scheduler, run_competitor_check, run_digest, run_npa_check
from src.storage.database import get_db
from src.updater.updater import DocumentUpdater, process_legal_updates
from src.web.routes import web_router



class UTF8JSONResponse(JSONResponse):
    """JSONResponse with ensure_ascii=False — Cyrillic returned as-is."""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(',', ':'),
        ).encode('utf-8')

logger = logging.getLogger("uvicorn.error")


# ── Request / Response models ────────────────────────────────────

class ScanRequest(BaseModel):
    url: str
    max_pages: int = 50
    organization_id: str | None = None


class ScanResponse(BaseModel):
    scan_id: str
    status: str
    pages_scanned: int
    forms_found: int
    external_scripts_found: int
    privacy_policy_found: bool
    cookie_banner_found: bool


class AnalyzeRequest(BaseModel):
    url: str
    max_pages: int = 50
    organization_id: str | None = None


class AnalyzeResponse(BaseModel):
    report_id: str
    site_url: str
    overall_score: int
    risk_level: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    violations_count: int
    critical_violations: int
    estimated_fine_min: int
    estimated_fine_max: int
    summary: str


class OrganizationRequest(BaseModel):
    legal_name: str
    website_url: str
    short_name: str = ""
    inn: str = ""
    ogrn: str = ""
    legal_address: str = ""
    actual_address: str = ""
    ceo_name: str = ""
    ceo_position: str = "Генеральный директор"
    responsible_person: str = ""
    responsible_contact: str = ""
    email: str = ""
    phone: str = ""
    data_categories: list[str] = Field(default_factory=list)
    processing_purposes: list[str] = Field(default_factory=list)
    data_subjects: list[str] = Field(default_factory=list)
    third_parties: list[str] = Field(default_factory=list)
    cross_border: bool = False
    cross_border_countries: list[str] = Field(default_factory=list)
    hosting_location: str = "Российская Федерация"
    info_systems: list[str] = Field(default_factory=list)


# ── Scheduled jobs ───────────────────────────────────────────────

async def _scheduled_process_updates():
    """Process all pending legal updates every 24h."""
    from src.knowledge.loader import load_legal_updates
    try:
        updates = load_legal_updates()
        if not updates:
            logger.info("Scheduler: no legal updates to process")
            return
        results = await process_legal_updates(updates, mode="confirm")
        logger.info("Scheduler: process_legal_updates — %d updates processed", len(updates))
    except Exception as exc:
        logger.error("Scheduler: process_legal_updates failed: %s", exc)


async def _scheduled_monitoring_cycle():
    """Run site monitoring cycle every 7 days."""
    try:
        new_updates = await run_monitoring_cycle()
        logger.info("Scheduler: run_monitoring_cycle — %d new updates found", len(new_updates))
    except Exception as exc:
        logger.error("Scheduler: run_monitoring_cycle failed: %s", exc)


# ── App ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await get_db()
    logger.info("Database initialized")

    scheduler = create_scheduler()
    scheduler.add_job(_scheduled_process_updates, "interval", hours=24, jitter=3600)
    scheduler.add_job(_scheduled_monitoring_cycle, "interval", weeks=1, jitter=3600)
    scheduler.start()
    logger.info("Scheduler started")

    yield

    scheduler.shutdown(wait=False)
    await db.close()


app = FastAPI(
    title="Compliance 152-ФЗ API",
    description="ИИ-агент для проверки соответствия сайтов требованиям 152-ФЗ",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Web UI ───────────────────────────────────────────────────────

_web_dir = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")


# ── Auth middleware ──────────────────────────────────────────────

@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    """Bearer token check. Only /api/v1/* routes require a token."""
    if not request.url.path.startswith("/api/v1/"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not auth[7:].strip():
        return UTF8JSONResponse(
            status_code=401,
            content={"detail": "Требуется авторизация. Укажите заголовок: Authorization: Bearer <token>"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # TODO: заменить на реальную проверку токена
    return await call_next(request)


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "compliance152"}


@app.post("/api/v1/scan", response_model=ScanResponse)
async def scan_site(request: ScanRequest):
    """Scan a website and return raw scan results."""
    scanner = SiteScanner(max_pages=request.max_pages)
    try:
        result = await scanner.scan(request.url)
    except Exception as e:
        logger.error("Scan failed for %s: %s", request.url, e)
        raise HTTPException(status_code=500, detail=f"Scan failed: {e}")

    scan_id = str(uuid.uuid4())
    db = await get_db()
    await db.save_scan(
        scan_id=scan_id,
        org_id=request.organization_id,
        url=request.url,
        result_json=result.model_dump_json(),
        pages=result.pages_scanned,
    )

    return ScanResponse(
        scan_id=scan_id,
        status="completed",
        pages_scanned=result.pages_scanned,
        forms_found=len(result.forms),
        external_scripts_found=len(result.external_scripts),
        privacy_policy_found=result.privacy_policy.found,
        cookie_banner_found=result.cookie_banner.found,
    )


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Scan + analyze a website for 152-FZ compliance."""
    # Step 1: Scan
    scanner = SiteScanner(max_pages=request.max_pages)
    try:
        scan_result = await scanner.scan(request.url)
    except Exception as e:
        logger.error("Scan failed for %s: %s", request.url, e)
        raise HTTPException(status_code=500, detail=f"Scan failed: {e}")

    scan_id = str(uuid.uuid4())
    db = await get_db()
    await db.save_scan(
        scan_id=scan_id,
        org_id=request.organization_id,
        url=request.url,
        result_json=scan_result.model_dump_json(),
        pages=scan_result.pages_scanned,
    )

    # Step 2: Analyze
    try:
        report = await analyze_site(scan_result)
    except Exception as e:
        logger.error("Analysis failed for %s: %s", request.url, e)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    # Save report
    report_data = report.model_dump(mode="json")
    report_data["scan_id"] = scan_id
    report_data["organization_id"] = request.organization_id
    await db.save_report(report_data)

    critical_count = sum(
        1 for v in report.violations if v.severity.value == "critical"
    )

    return AnalyzeResponse(
        report_id=report.id,
        site_url=report.site_url,
        overall_score=report.overall_score,
        risk_level=report.risk_level.value,
        total_checks=report.total_checks,
        passed_checks=report.passed_checks,
        failed_checks=report.failed_checks,
        violations_count=len(report.violations),
        critical_violations=critical_count,
        estimated_fine_min=report.fine_estimate.min_total,
        estimated_fine_max=report.fine_estimate.max_total,
        summary=report.summary,
    )


@app.get("/api/v1/report/{report_id}")
async def get_report(report_id: str):
    """Get full compliance report by ID."""
    db = await get_db()
    reports = await db.list_reports()
    for r in reports:
        if r.get("id") == report_id:
            report_json = r.get("report_json")
            if report_json:
                return json.loads(report_json)
            return r
    raise HTTPException(status_code=404, detail="Report not found")


@app.get("/api/v1/scan/{scan_id}")
async def get_scan(scan_id: str):
    """Get scan result by ID."""
    db = await get_db()
    scan = await db.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    result = scan.get("result_json")
    if result:
        return json.loads(result)
    return scan


@app.get("/api/v1/organizations")
async def list_organizations(limit: int = 50):
    """List all organizations."""
    db = await get_db()
    orgs = await db.list_organizations(limit=limit)
    return {"organizations": orgs, "count": len(orgs)}


@app.post("/api/v1/organizations")
async def create_organization(request: OrganizationRequest):
    """Create an organization (client)."""
    org_id = str(uuid.uuid4())
    db = await get_db()
    org_data = request.model_dump()
    org_data["id"] = org_id
    org_data["created_at"] = datetime.utcnow().isoformat()
    await db.save_organization(org_data)
    return {
        "id": org_id,
        "status": "created",
        "legal_name": request.legal_name,
        "inn": request.inn,
        "website_url": request.website_url,
        "created_at": org_data["created_at"],
    }


@app.get("/api/v1/organizations/{org_id}")
async def get_organization(org_id: str):
    """Get organization by ID."""
    db = await get_db()
    org = await db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@app.get("/api/v1/reports")
async def list_reports(organization_id: str | None = None, limit: int = 20):
    """List compliance reports."""
    db = await get_db()
    reports = await db.list_reports(org_id=organization_id, limit=limit)
    return {"reports": reports, "count": len(reports)}


# ── Document Generation ─────────────────────────────────────────


class GenerateRequest(BaseModel):
    organization_id: str
    document_types: list[str] | None = None  # None = full package
    doc_types: list[str] | None = None  # backward-compat alias

    def get_doc_types(self) -> list[str] | None:
        """Return document_types if set, else doc_types (backward compat)."""
        return self.document_types if self.document_types is not None else self.doc_types


class GeneratePublicRequest(BaseModel):
    organization_id: str


@app.get("/api/v1/documents/types")
async def list_document_types():
    """List all available document types."""
    return {
        "types": {
            k: {"title": v["title"], "description": v["description"]}
            for k, v in DOCUMENT_TYPES.items()
        },
        "public_documents": PUBLIC_DOCUMENTS,
        "full_package": FULL_PACKAGE,
    }


@app.post("/api/v1/documents/generate")
async def generate_docs(request: GenerateRequest):
    """Generate documents for an organization."""
    db = await get_db()
    org_data = await db.get_organization(request.organization_id)
    if not org_data:
        raise HTTPException(status_code=404, detail="Organization not found")

    org = OrganizationData(**org_data)
    docs = await generate_documents(org, request.get_doc_types())

    # Save generated documents
    saved = []
    for doc in docs:
        if "error" in doc:
            saved.append(doc)
            continue
        await db.save_document(doc)
        saved.append({
            "id": doc["id"],
            "doc_type": doc["doc_type"],
            "title": doc["title"],
            "status": "generated",
        })

    return {
        "organization_id": request.organization_id,
        "documents": saved,
        "total": len(saved),
        "successful": sum(1 for d in saved if "error" not in d),
    }


@app.post("/api/v1/documents/generate/public")
async def generate_public_docs(request: GeneratePublicRequest):
    """Generate only public-facing documents (privacy policy, consent, cookie policy)."""
    db = await get_db()
    org_data = await db.get_organization(request.organization_id)
    if not org_data:
        raise HTTPException(status_code=404, detail="Organization not found")

    org = OrganizationData(**org_data)
    docs = await generate_documents(org, PUBLIC_DOCUMENTS)

    for doc in docs:
        if "error" not in doc:
            await db.save_document(doc)

    return {
        "organization_id": request.organization_id,
        "documents": [
            {"id": d.get("id"), "doc_type": d["doc_type"], "title": d.get("title", "")}
            for d in docs if "error" not in d
        ],
        "errors": [d for d in docs if "error" in d],
    }


# ── Document Export ───────────────────────────────────────────


@app.get("/api/v1/documents/{org_id}/export/pdf")
async def export_all_documents_pdf(org_id: str):
    """Export all documents for an organization as a single merged PDF."""
    db = await get_db()
    docs = await db.get_documents(org_id)
    if not docs:
        raise HTTPException(status_code=404, detail="No documents found for this organization")

    org = await db.get_organization(org_id)
    org_name = org.get("legal_name", "") if org else ""

    try:
        pdf_bytes = create_merged_pdf(docs, organization_name=org_name)
    except Exception as e:
        logger.error("PDF export failed for %s: %s", org_id, e)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    filename = f"documents_{org_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/v1/documents/{org_id}/{doc_type}/export/docx")
async def export_document_docx(org_id: str, doc_type: str):
    """Export a single document as DOCX."""
    db = await get_db()
    docs = await db.get_documents(org_id)
    doc = None
    for d in docs:
        if d.get("doc_type") == doc_type:
            doc = d
            break
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {doc_type} not found")

    org = await db.get_organization(org_id)
    org_name = org.get("legal_name", "") if org else ""

    try:
        converter = MarkdownToDocxConverter(
            title=doc.get("title", ""),
            organization_name=org_name,
        )
        docx_bytes = converter.convert_to_bytes(doc["content_md"])
    except Exception as e:
        logger.error("DOCX export failed for %s/%s: %s", org_id, doc_type, e)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    filename = f"{doc_type}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/v1/documents/{org_id}/{doc_type}/export/pdf")
async def export_document_pdf(org_id: str, doc_type: str):
    """Export a single document as PDF."""
    db = await get_db()
    docs = await db.get_documents(org_id)
    doc = None
    for d in docs:
        if d.get("doc_type") == doc_type:
            doc = d
            break
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {doc_type} not found")

    org = await db.get_organization(org_id)
    org_name = org.get("legal_name", "") if org else ""

    try:
        converter = MarkdownToPdfConverter(
            title=doc.get("title", ""),
            organization_name=org_name,
        )
        pdf_bytes = converter.convert_to_bytes(doc["content_md"])
    except Exception as e:
        logger.error("PDF export failed for %s/%s: %s", org_id, doc_type, e)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    filename = f"{doc_type}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Document Retrieval ────────────────────────────────────────


@app.get("/api/v1/documents/{org_id}")
async def get_documents(org_id: str):
    """Get all generated documents for an organization."""
    db = await get_db()
    docs = await db.get_documents(org_id)
    return {"documents": docs, "count": len(docs)}


@app.get("/api/v1/documents/{org_id}/{doc_type}")
async def get_document_by_type(org_id: str, doc_type: str):
    """Get a specific document by type for an organization."""
    db = await get_db()
    docs = await db.get_documents(org_id)
    for doc in docs:
        if doc.get("doc_type") == doc_type:
            return doc
    raise HTTPException(status_code=404, detail=f"Document {doc_type} not found")


# ── Legal Monitoring & Updates ─────────────────────────────────


@app.post("/api/v1/monitor/check")
async def check_legal_updates():
    """Run a monitoring cycle to discover new 152-FZ changes.

    Searches the web and RKN for recent legal updates,
    analyzes them with LLM, and saves new findings.
    """
    try:
        new_updates = await run_monitoring_cycle()
        return {
            "status": "completed",
            "new_updates_found": len(new_updates),
            "updates": [
                {
                    "id": u.id,
                    "title": u.title,
                    "severity": u.severity,
                    "effective_date": u.effective_date,
                    "affected_documents": u.affected_documents,
                }
                for u in new_updates
            ],
        }
    except Exception as e:
        logger.error("Monitoring check failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Monitoring failed: {e}")


class AnalyzeLegalTextRequest(BaseModel):
    text: str
    source: str = ""
    source_url: str = ""


@app.post("/api/v1/legal-updates/analyze")
async def analyze_legal_text(request: AnalyzeLegalTextRequest):
    """Analyze a legal text (law, amendment, RKN clarification) for 152-FZ impact.

    Upload the text of a new law/amendment and get structured analysis of
    how it affects compliance documents.
    """
    from src.monitor.monitor import LegalMonitor

    try:
        from src.llm.client import call_llm
        from src.monitor.monitor import MONITOR_ANALYSIS_SYSTEM

        raw_response = await call_llm(
            system_prompt=MONITOR_ANALYSIS_SYSTEM,
            user_prompt=(
                f"Проанализируй следующий правовой акт:\n\n"
                f"Источник: {request.source}\n"
                f"URL: {request.source_url}\n\n"
                f"Текст:\n{request.text[:15000]}\n\n"
                f"Определи все изменения, влияющие на документы по 152-ФЗ."
            ),
            max_tokens=4096,
            temperature=0.1,
        )

        monitor = LegalMonitor()
        updates_raw = monitor._parse_llm_response(raw_response)

        from src.models.legal_update import LegalUpdate
        updates = []
        for item in updates_raw:
            try:
                updates.append(LegalUpdate(**item))
            except Exception:
                pass

        return {
            "status": "analyzed",
            "updates_found": len(updates),
            "updates": [u.model_dump(mode="json") for u in updates],
            "raw_analysis": raw_response,
        }
    except Exception as e:
        logger.error("Legal text analysis failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


class ProcessUpdatesRequest(BaseModel):
    update_ids: list[str] | None = None  # None = process all pending
    mode: str = "confirm"  # "auto" | "confirm"


@app.post("/api/v1/legal-updates/process")
async def process_updates(request: ProcessUpdatesRequest):
    """Process legal updates: find affected documents and create new versions.

    Mode 'confirm' creates draft versions for review.
    Mode 'auto' updates documents immediately.
    """
    from src.knowledge.loader import load_legal_updates

    try:
        all_updates = load_legal_updates()

        if request.update_ids:
            updates = [u for u in all_updates if u.id in request.update_ids]
        else:
            updates = all_updates

        if not updates:
            return {"status": "no_updates", "results": []}

        results = await process_legal_updates(updates, mode=request.mode)
        return {
            "status": "processed",
            "updates_processed": len(updates),
            "results": results,
        }
    except Exception as e:
        logger.error("Update processing failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")


@app.get("/api/v1/legal-updates")
async def list_legal_updates():
    """List all known legal updates from the knowledge base."""
    from src.knowledge.loader import load_legal_updates

    updates = load_legal_updates()
    return {
        "updates": [u.model_dump(mode="json") for u in updates],
        "count": len(updates),
    }


# ── Competitor Intelligence Monitor — manual triggers ────────────


@app.post("/api/v1/monitor/run-npa")
async def manual_run_npa():
    """Manually trigger NPA sources check and send critical alerts."""
    try:
        alerts = await run_npa_check()
        return {
            "status": "completed",
            "critical_alerts": len(alerts),
            "alerts": alerts,
        }
    except Exception as exc:
        logger.error("manual_run_npa failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"NPA check failed: {exc}")


@app.post("/api/v1/monitor/run-competitors")
async def manual_run_competitors():
    """Manually trigger competitor pages check and LLM analysis."""
    try:
        count = await run_competitor_check()
        return {
            "status": "completed",
            "analysed_changes": count,
        }
    except Exception as exc:
        logger.error("manual_run_competitors failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Competitor check failed: {exc}")


@app.get("/api/v1/monitor/status")
async def monitor_status(limit: int = 20):
    """Return recent competitor/NPA changes and latest digest."""
    db = await get_db()
    changes = await db.list_pending_changes(limit=limit)
    latest_digest = await db.get_latest_digest()
    return {
        "pending_changes": changes,
        "pending_count": len(changes),
        "latest_digest": latest_digest,
    }


# ── Web Verification Cache ───────────────────────────────────────


@app.get("/api/v1/cache/stats")
async def cache_stats():
    """Get web context cache statistics."""
    cache = get_cache()
    return cache.stats()


@app.post("/api/v1/cache/clear")
async def clear_cache():
    """Clear the web context cache to force fresh searches."""
    cache = get_cache()
    size_before = cache.size
    cache.clear()
    return {"status": "cleared", "entries_removed": size_before}


# ── Web UI routes (must be last to avoid conflicts with API routes) ──

app.include_router(web_router)
